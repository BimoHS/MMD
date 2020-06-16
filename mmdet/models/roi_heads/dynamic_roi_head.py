import torch

from mmdet.core import bbox2roi
from mmdet.models.losses import SmoothL1Loss
from ..builder import HEADS
from .standard_roi_head import StandardRoIHead


@HEADS.register_module()
class DynamicRoIHead(StandardRoIHead):
    """RoI head for Dynamic R-CNN

    https://arxiv.org/abs/2004.06002
    """

    def __init__(self, k_i=75, k_e=10, iteration_count=100, **kwargs):
        super(DynamicRoIHead, self).__init__(**kwargs)
        self.k_i = k_i
        self.k_e = k_e
        self.iteration_count = iteration_count
        self.initial_iou = 0.4
        self.initial_beta = 1.0
        self.cur_iou = []
        self.cur_beta = []

    def forward_train(self,
                      x,
                      img_metas,
                      proposal_list,
                      gt_bboxes,
                      gt_labels,
                      gt_bboxes_ignore=None,
                      gt_masks=None):
        """
        Args:
            x (list[Tensor]): list of multi-level img features.

            img_metas (list[dict]): list of image info dict where each dict
                has: 'img_shape', 'scale_factor', 'flip', and may also contain
                'filename', 'ori_shape', 'pad_shape', and 'img_norm_cfg'.
                For details on the values of these keys see
                `mmdet/datasets/pipelines/formatting.py:Collect`.

            proposals (list[Tensors]): list of region proposals.

            gt_bboxes (list[Tensor]): each item are the truth boxes for each
                image in [tl_x, tl_y, br_x, br_y] format.

            gt_labels (list[Tensor]): class indices corresponding to each box

            gt_bboxes_ignore (None | list[Tensor]): specify which bounding
                boxes can be ignored when computing the loss.

            gt_masks (None | Tensor) : true segmentation masks for each box
                used if the architecture supports a segmentation task.

        Returns:
            dict[str, Tensor]: a dictionary of loss components
        """
        # assign gts and sample proposals
        if self.with_bbox or self.with_mask:
            num_imgs = len(img_metas)
            if gt_bboxes_ignore is None:
                gt_bboxes_ignore = [None for _ in range(num_imgs)]
            sampling_results = []
            cur_iou = []
            for i in range(num_imgs):
                assign_result = self.bbox_assigner.assign(
                    proposal_list[i], gt_bboxes[i], gt_bboxes_ignore[i],
                    gt_labels[i])
                sampling_result = self.bbox_sampler.sample(
                    assign_result,
                    proposal_list[i],
                    gt_bboxes[i],
                    gt_labels[i],
                    feats=[lvl_feat[i][None] for lvl_feat in x])
                cur_iou.append(
                    torch.topk(assign_result.max_overlaps,
                               min(self.k_i, len(
                                   assign_result.max_overlaps)))[0][-1].item())
                sampling_results.append(sampling_result)
            cur_iou = sum(cur_iou) / num_imgs
            self.cur_iou.append(cur_iou)

        losses = dict()
        # bbox head forward and loss
        if self.with_bbox:
            bbox_results = self._bbox_forward_train(x, sampling_results,
                                                    gt_bboxes, gt_labels,
                                                    img_metas)
            losses.update(bbox_results['loss_bbox'])

        # mask head forward and loss
        if self.with_mask:
            mask_results = self._mask_forward_train(x, sampling_results,
                                                    bbox_results['bbox_feats'],
                                                    gt_masks, img_metas)
            # TODO: Support empty tensor input. #2280
            if mask_results['loss_mask'] is not None:
                losses.update(mask_results['loss_mask'])

        # update IoU threshold and SmoothL1 beta
        if len(self.cur_iou) % self.iteration_count == 0:
            new_iou_thr, new_beta = self.update_statistics()

        return losses

    def _bbox_forward_train(self, x, sampling_results, gt_bboxes, gt_labels,
                            img_metas):
        num_imgs = len(img_metas)
        rois = bbox2roi([res.bboxes for res in sampling_results])
        bbox_results = self._bbox_forward(x, rois)

        bbox_targets = self.bbox_head.get_targets(sampling_results, gt_bboxes,
                                                  gt_labels, self.train_cfg)
        pos_inds = bbox_targets[3][:, 0].nonzero().squeeze(1)
        num_pos = len(pos_inds)
        cur_target = bbox_targets[2][pos_inds, :2].abs().mean(dim=1)
        cur_target = torch.kthvalue(cur_target.cpu(),
                                    min(self.k_e * num_imgs,
                                        num_pos))[0].item()
        self.cur_beta.append(cur_target)
        loss_bbox = self.bbox_head.loss(bbox_results['cls_score'],
                                        bbox_results['bbox_pred'], rois,
                                        *bbox_targets)

        bbox_results.update(loss_bbox=loss_bbox)
        return bbox_results

    def update_statistics(self):
        new_iou_thr = max(self.initial_iou,
                          sum(self.cur_iou) / self.iteration_count)
        self.cur_iou = []
        self.bbox_assigner.pos_iou_thr = new_iou_thr
        self.bbox_assigner.neg_iou_thr = new_iou_thr
        self.bbox_assigner.min_pos_iou = new_iou_thr
        new_beta = min(self.initial_beta,
                       sorted(self.cur_beta)[self.iteration_count // 2])
        self.cur_beta = []
        assert isinstance(self.bbox_head.loss_bbox, SmoothL1Loss)
        self.bbox_head.loss_bbox.beta = new_beta
        return new_iou_thr, new_beta
