import mmcv
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from mmcv.cnn import ConvModule

from mmdet.core import matrix_nms, multi_apply, points_nms
from mmdet.core.results.results import DetectionResults
from mmdet.models.builder import HEADS, build_loss
from .base_mask_head import BaseMaskHead


def center_of_mass(mask):
    h, w = mask.shape
    grid_h = torch.arange(h, device=mask.device)[:, None]
    grid_w = torch.arange(w, device=mask.device)
    normalizer = mask.sum().float().clamp(min=1e-6)
    center_h = (mask * grid_h).sum() / normalizer
    center_w = (mask * grid_w).sum() / normalizer
    return center_h, center_w


@HEADS.register_module()
class SOLOHead(BaseMaskHead):
    """SOLO mask head used in  https://arxiv.org/abs/1912.04488.

    Note that although SOLO head is single-stage instance segmentors,
    it still uses gt_bbox for calculation while getting target, but it
    does not use gt_bbox when calculating loss.

    Args:
        num_classes (int): Number of categories excluding the background
            category.
        in_channels (int): Number of channels in the input feature map.
        feat_channels (int): Number of hidden channels. Used in child classes.
            Default: 256
        stacked_convs (int): Number of stacking convs of the head.
            Default: 4
        strides (tuple): Downsample factor of each feature map.
        scale_ranges (tuple[tuple[int, int]]): Area range of multiple
            level mask.
        pos_scale (float): Constant scale factor to control the center region.
        num_grids (list): Divided image into a uniform grids, each feature map
            has a different grid value. The number of output channels is
            grid ** 2. Default: [40, 36, 24, 16, 12]
        cls_down_index (int): The index of downsample operation in
            classification branch. Default: 0.
        loss_mask (dict): Config of mask loss.
        loss_cls (dict): Config of classification loss.
        norm_cfg (dict): dictionary to construct and config norm layer.
            Default: norm_cfg=dict(type='GN', num_groups=32,
                                   requires_grad=True).
        train_cfg (dict): Training config of head.
        test_cfg (dict): Testing config of head.
        init_cfg (dict or list[dict], optional): Initialization config dict.
    """

    def __init__(
        self,
        num_classes,
        in_channels,
        feat_channels=256,
        stacked_convs=4,
        strides=(4, 8, 16, 32, 64),
        scale_ranges=((8, 32), (16, 64), (32, 128), (64, 256), (128, 512)),
        pos_scale=0.2,
        num_grids=[40, 36, 24, 16, 12],
        cls_down_index=0,
        loss_mask=None,
        loss_cls=None,
        norm_cfg=dict(type='GN', num_groups=32, requires_grad=True),
        train_cfg=None,
        test_cfg=None,
        init_cfg=[
            dict(type='Normal', layer='Conv2d', std=0.01),
            dict(
                type='Normal',
                std=0.01,
                bias_prob=0.01,
                override=dict(name='conv_mask_list')),
            dict(
                type='Normal',
                std=0.01,
                bias_prob=0.01,
                override=dict(name='conv_cls'))
        ],
    ):
        super(SOLOHead, self).__init__(init_cfg)
        self.num_classes = num_classes
        self.cls_out_channels = self.num_classes
        self.in_channels = in_channels
        self.feat_channels = feat_channels
        self.stacked_convs = stacked_convs
        self.strides = strides
        self.num_grids = num_grids
        # number of FPN feats
        self.num_levels = len(strides)
        assert self.num_levels == len(self.strides)
        self.scale_ranges = scale_ranges
        self.pos_scale = pos_scale

        self.cls_down_index = cls_down_index
        self.loss_cls = build_loss(loss_cls)
        self.loss_mask = build_loss(loss_mask)
        self.norm_cfg = norm_cfg
        self.init_cfg = init_cfg
        self.train_cfg = train_cfg
        self.test_cfg = test_cfg
        self._init_layers()

    def _init_layers(self):
        self.mask_convs = nn.ModuleList()
        self.cls_convs = nn.ModuleList()
        for i in range(self.stacked_convs):
            chn = self.in_channels + 2 if i == 0 else self.feat_channels
            self.mask_convs.append(
                ConvModule(
                    chn,
                    self.feat_channels,
                    3,
                    stride=1,
                    padding=1,
                    norm_cfg=self.norm_cfg,
                    bias=self.norm_cfg is None))
            chn = self.in_channels if i == 0 else self.feat_channels
            self.cls_convs.append(
                ConvModule(
                    chn,
                    self.feat_channels,
                    3,
                    stride=1,
                    padding=1,
                    norm_cfg=self.norm_cfg,
                    bias=self.norm_cfg is None))
        self.conv_mask_list = nn.ModuleList()
        for num_grid in self.num_grids:
            self.conv_mask_list.append(
                nn.Conv2d(self.feat_channels, num_grid**2, 1))

        self.conv_cls = nn.Conv2d(
            self.feat_channels, self.cls_out_channels, 3, padding=1)

    def resize_feats(self, feats):

        return (F.interpolate(feats[0], scale_factor=0.5,
                              mode='bilinear'), feats[1], feats[2], feats[3],
                F.interpolate(
                    feats[4], size=feats[3].shape[-2:], mode='bilinear'))

    def forward(self, feats):

        assert len(feats) == self.num_levels
        feats = self.resize_feats(feats)
        mask_preds = []
        cls_preds = []
        for i in range(self.num_levels):
            x = feats[i]
            mask_feat = x
            cls_feat = x
            # generate and concat the coordinate
            x_range = torch.linspace(
                -1, 1, mask_feat.shape[-1], device=mask_feat.device)
            y_range = torch.linspace(
                -1, 1, mask_feat.shape[-2], device=mask_feat.device)
            y, x = torch.meshgrid(y_range, x_range)
            y = y.expand([mask_feat.shape[0], 1, -1, -1])
            x = x.expand([mask_feat.shape[0], 1, -1, -1])
            coord_feat = torch.cat([x, y], 1)
            mask_feat = torch.cat([mask_feat, coord_feat], 1)

            for mask_layer in (self.mask_convs):
                mask_feat = mask_layer(mask_feat)

            mask_feat = F.interpolate(
                mask_feat, scale_factor=2, mode='bilinear')
            mask_pred = self.conv_mask_list[i](mask_feat)

            # cls branch
            for j, cls_layer in enumerate(self.cls_convs):
                if j == self.cls_down_index:
                    num_grid = self.num_grids[i]
                    cls_feat = F.interpolate(
                        cls_feat, size=num_grid, mode='bilinear')
                cls_feat = cls_layer(cls_feat)

            cls_pred = self.conv_cls(cls_feat)
            if not self.training:
                feat_wh = feats[0].size()[-2:]
                upsampled_size = (feat_wh[0] * 2, feat_wh[1] * 2)
                mask_pred = F.interpolate(
                    mask_pred.sigmoid(), size=upsampled_size, mode='bilinear')
                cls_pred = points_nms(
                    cls_pred.sigmoid(), kernel=2).permute(0, 2, 3, 1)
            mask_preds.append(mask_pred)
            cls_preds.append(cls_pred)
        return mask_preds, cls_preds

    def loss(self,
             mlvl_mask_preds,
             mlvl_cls_preds,
             gt_labels,
             gt_masks,
             img_metas,
             gt_bboxes=None,
             gt_bboxes_ignore=None,
             **kwargs):

        num_levels = self.num_levels
        num_imgs = len(gt_labels)

        featmap_sizes = [featmap.size()[-2:] for featmap in mlvl_mask_preds]
        mask_targets, labels, pos_masks = multi_apply(
            self._get_targets_single,
            gt_bboxes,
            gt_labels,
            gt_masks,
            featmap_sizes=featmap_sizes)

        # before outside list means multi images
        # after outside list means multi levels
        mlvl_pos_mask_targets = [[] for _ in range(num_levels)]
        mlvl_pos_mask_preds = [[] for _ in range(num_levels)]
        mlvl_pos_masks = [[] for _ in range(num_levels)]
        mlvl_labels = [[] for _ in range(num_levels)]
        for img_id in range(num_imgs):
            assert num_levels == len(mask_targets[img_id])
            for lvl in range(num_levels):
                mlvl_pos_mask_targets[lvl].append(
                    mask_targets[img_id][lvl][pos_masks[img_id][lvl], ...])
                mlvl_pos_mask_preds[lvl].append(
                    mlvl_mask_preds[lvl][img_id, pos_masks[img_id][lvl], ...])
                mlvl_pos_masks[lvl].append(pos_masks[img_id][lvl].flatten())
                mlvl_labels[lvl].append(labels[img_id][lvl].flatten())

        temp_mlvl_cls_preds = []
        for lvl in range(num_levels):
            mlvl_pos_mask_targets[lvl] = torch.cat(
                mlvl_pos_mask_targets[lvl], dim=0)
            mlvl_pos_mask_preds[lvl] = torch.cat(
                mlvl_pos_mask_preds[lvl], dim=0)
            mlvl_pos_masks[lvl] = torch.cat(mlvl_pos_masks[lvl], dim=0)
            mlvl_labels[lvl] = torch.cat(mlvl_labels[lvl], dim=0)
            temp_mlvl_cls_preds.append(mlvl_cls_preds[lvl].permute(
                0, 2, 3, 1).reshape(-1, self.cls_out_channels))

        # ins
        flatten_pos_masks = torch.cat(mlvl_pos_masks)
        num_pos = flatten_pos_masks.sum()

        # dice loss
        loss_mask = []
        for pred, target in zip(mlvl_pos_mask_preds, mlvl_pos_mask_targets):
            if pred.size()[0] == 0:
                loss_mask.append(pred.sum().unsqueeze(0))
                continue
            loss_mask.append(self.loss_mask(pred, target))
        if num_pos > 0:
            loss_mask = torch.cat(loss_mask).sum() / num_pos
        else:
            loss_mask = torch.cat(loss_mask).mean()

        flatten_labels = torch.cat(mlvl_labels)
        flatten_cls_preds = torch.cat(temp_mlvl_cls_preds)
        loss_cls = self.loss_cls(
            flatten_cls_preds, flatten_labels, avg_factor=num_pos + 1)
        return dict(loss_ins=loss_mask, loss_cate=loss_cls)

    def _get_targets_single(self,
                            gt_bboxes,
                            gt_labels,
                            gt_masks,
                            featmap_sizes=None):
        """Compute targets for predictions of single image.

        Args:
            gt_bboxes (Tensor): Ground truth bbox of each instance,
                shape (num_gts, 4).
            gt_labels (Tensor): Ground truth label of each instance,
                shape (num_gts,).
            gt_masks (Tensor): Ground truth mask of each instance,
                shape (num_gts, h, w).
            featmap_sizes (list[:obj:`torch.size`]): Size of each
                feature map from feature pyramid, each element
                means (feat_h, feat_w). Default: None.

        Returns:
            Tuple: Usually returns a tuple containing targets for predictions.

                - mlvl_mask_targets (list[Tensor]): Each element represent
                    the binary mask targets for all points in this
                    level, has shape (num_grid**2, out_h, out_w)
                - mlvl_labels (list[Tensor]): Each element is
                    classification labels for all
                    points in this level, has shape
                    (num_grid, num_grid)
                - mlvl_pos_masks (list[Tensor]): Each element is
                    a `BoolTensor` to represent whether the
                    corresponding point in single level
                    is positive, has shape (num_grid **2)
        """
        device = gt_labels[0].device
        gt_areas = torch.sqrt((gt_bboxes[:, 2] - gt_bboxes[:, 0]) *
                              (gt_bboxes[:, 3] - gt_bboxes[:, 1]))

        mlvl_mask_targets = []
        mlvl_labels = []
        mlvl_pos_masks = []
        for (lower_bound, upper_bound), stride, featmap_size, num_grid \
                in zip(self.scale_ranges, self.strides,
                       featmap_sizes, self.num_grids):

            mask_target = torch.zeros(
                [num_grid**2, featmap_size[0], featmap_size[1]],
                dtype=torch.uint8,
                device=device)
            # FG cat_id: [0, num_classes -1], BG cat_id: num_classes
            labels = torch.zeros([num_grid, num_grid],
                                 dtype=torch.int64,
                                 device=device) + self.num_classes
            pos_mask = torch.zeros([num_grid**2],
                                   dtype=torch.bool,
                                   device=device)

            gt_inds = ((gt_areas >= lower_bound) &
                       (gt_areas <= upper_bound)).nonzero().flatten()
            if len(gt_inds) == 0:
                mlvl_mask_targets.append(mask_target)
                mlvl_labels.append(labels)
                mlvl_pos_masks.append(pos_mask)
                continue
            hit_gt_bboxes = gt_bboxes[gt_inds]
            hit_gt_labels = gt_labels[gt_inds]
            hit_gt_masks = gt_masks[gt_inds, ...]

            pos_w_ranges = 0.5 * (hit_gt_bboxes[:, 2] -
                                  hit_gt_bboxes[:, 0]) * self.pos_scale
            pos_h_ranges = 0.5 * (hit_gt_bboxes[:, 3] -
                                  hit_gt_bboxes[:, 1]) * self.pos_scale

            # mass center
            valid_mask_flags = hit_gt_masks.sum(dim=-1).sum(dim=-1) > 0
            output_stride = stride / 2

            for gt_mask, gt_label, pos_h_range, pos_w_range, \
                valid_mask_flag in \
                    zip(hit_gt_masks, hit_gt_labels, pos_h_ranges,
                        pos_w_ranges, valid_mask_flags):
                if not valid_mask_flag:
                    continue
                upsampled_size = (featmap_sizes[0][0] * 4,
                                  featmap_sizes[0][1] * 4)
                center_h, center_w = center_of_mass(gt_mask)

                coord_w = int(
                    (center_w / upsampled_size[1]) // (1. / num_grid))
                coord_h = int(
                    (center_h / upsampled_size[0]) // (1. / num_grid))

                # left, top, right, down
                top_box = max(
                    0,
                    int(((center_h - pos_h_range) / upsampled_size[0]) //
                        (1. / num_grid)))
                down_box = min(
                    num_grid - 1,
                    int(((center_h + pos_h_range) / upsampled_size[0]) //
                        (1. / num_grid)))
                left_box = max(
                    0,
                    int(((center_w - pos_w_range) / upsampled_size[1]) //
                        (1. / num_grid)))
                right_box = min(
                    num_grid - 1,
                    int(((center_w + pos_w_range) / upsampled_size[1]) //
                        (1. / num_grid)))

                top = max(top_box, coord_h - 1)
                down = min(down_box, coord_h + 1)
                left = max(coord_w - 1, left_box)
                right = min(right_box, coord_w + 1)

                labels[top:(down + 1), left:(right + 1)] = gt_label
                # ins
                gt_mask = np.uint8(gt_mask.cpu().numpy())
                gt_mask = mmcv.imrescale(gt_mask, scale=1. / output_stride)
                gt_mask = torch.from_numpy(gt_mask).to(device=device)

                for i in range(top, down + 1):
                    for j in range(left, right + 1):
                        index = int(i * num_grid + j)
                        mask_target[index, :gt_mask.shape[0], :gt_mask.
                                    shape[1]] = gt_mask
                        pos_mask[index] = True
            mlvl_mask_targets.append(mask_target)
            mlvl_labels.append(labels)
            mlvl_pos_masks.append(pos_mask)
        return mlvl_mask_targets, mlvl_labels, mlvl_pos_masks

    def get_masks(self,
                  mask_preds,
                  cls_preds,
                  img_metas,
                  rescale=None,
                  **kwargs):
        """

        Args:
            mask_preds:
            cls_preds:
            img_metas:
            rescale:
            **kwargs:

        Returns:
            list[:obj:`DetectionResults`]
        """
        assert len(mask_preds) == len(cls_preds)
        num_levels = len(cls_preds)

        results_list = []
        for img_id in range(len(img_metas)):
            cls_pred_list = [
                cls_preds[lvl][img_id].view(-1, self.cls_out_channels)
                for lvl in range(num_levels)
            ]
            mask_pred_list = [
                mask_preds[lvl][img_id] for lvl in range(num_levels)
            ]

            cls_pred_list = torch.cat(cls_pred_list, dim=0)
            mask_pred_list = torch.cat(mask_pred_list, dim=0)

            results = self._get_masks_single(
                cls_pred_list, mask_pred_list, img_meta=img_metas[img_id])
            results_list.append(results)

        return results_list

    def _get_masks_single(self, cls_preds, mask_preds, img_meta, cfg=None):
        """Get processed mask related results of single image.

        Args:
            cls_preds (Tensor): Classification score of all points
                in single image, has shape (num_points, num_classes).
            mask_preds (Tensor): Mask prediction of all points in
                single image, has shape (num_points, feat_h, feat_w).
            img_meta (dict): Meta information of corresponding image.
            cfg (dict): Config used in test phase.

        Returns:
            :obj:`DetectionResults`: Processed results. Usually
            contains following keys.

                - scores (Tensor): Classification scores, has shape
                    (num_instance,)
                - labels (Tensor): Has shape (num_instances,).
                - masks (Tensor): Processed mask results, has
                    shape (num_instances, h, w).
        """

        def empty_results(results, cls_scores):
            results.scores = cls_scores.new_ones(0)
            results.masks = cls_scores.new_zeros(0, *results.ori_shape[:2])
            results.labels = cls_scores.new_ones(0)
            return results

        cfg = self.test_cfg if cfg is None else cfg
        assert len(cls_preds) == len(mask_preds)
        results = DetectionResults(img_meta, num_classes=self.num_classes)

        featmap_size = mask_preds.size()[-2:]

        img_shape = results.img_shape
        ori_shape = results.ori_shape

        h, w, _ = img_shape
        upsampled_size = (featmap_size[0] * 4, featmap_size[1] * 4)

        score_mask = (cls_preds > cfg.score_thr)
        cls_scores = cls_preds[score_mask]

        if len(cls_scores) == 0:
            return empty_results(results, cls_scores)

        inds = score_mask.nonzero()
        cls_labels = inds[:, 1]

        # Filter the mask which area is smaller than
        # stride
        lvl_inteval = cls_labels.new_tensor(self.num_grids).pow(2).cumsum(0)
        strides = cls_scores.new_ones(lvl_inteval[-1])
        strides[:lvl_inteval[0]] *= self.strides[0]
        for lvl in range(1, self.num_levels):
            strides[lvl_inteval[lvl - 1]:lvl_inteval[lvl]] *= self.strides[lvl]
        strides = strides[inds[:, 0]]
        mask_preds = mask_preds[inds[:, 0]]
        masks = mask_preds > cfg.mask_thr
        sum_masks = masks.sum((1, 2)).float()
        keep = sum_masks > strides
        if keep.sum() == 0:
            return empty_results(results, cls_scores)

        masks = masks[keep, ...]
        mask_preds = mask_preds[keep, ...]
        sum_masks = sum_masks[keep]
        cls_scores = cls_scores[keep]
        cls_labels = cls_labels[keep]

        # maskness.
        mask_scores = (mask_preds * masks.float()).sum((1, 2)) / sum_masks
        cls_scores *= mask_scores

        # sort and keep top nms_pre
        sort_inds = torch.argsort(cls_scores, descending=True)
        if len(sort_inds) > cfg.nms_pre:
            sort_inds = sort_inds[:cfg.nms_pre]
        masks = masks[sort_inds, :, :]
        mask_preds = mask_preds[sort_inds, :, :]
        sum_masks = sum_masks[sort_inds]
        cls_scores = cls_scores[sort_inds]
        cls_labels = cls_labels[sort_inds]

        # Matrix NMS
        cls_scores = matrix_nms(
            masks,
            cls_labels,
            cls_scores,
            kernel=cfg.kernel,
            sigma=cfg.sigma,
            sum_masks=sum_masks)

        # filter.
        keep = cls_scores >= cfg.update_thr
        if not keep.any():
            return empty_results(results, cls_scores)
        mask_preds = mask_preds[keep, :, :]
        cls_scores = cls_scores[keep]
        cls_labels = cls_labels[keep]

        # sort and keep top_k
        sort_inds = torch.argsort(cls_scores, descending=True)
        if len(sort_inds) > cfg.max_per_img:
            sort_inds = sort_inds[:cfg.max_per_img]
        mask_preds = mask_preds[sort_inds, :, :]
        cls_scores = cls_scores[sort_inds]
        cls_labels = cls_labels[sort_inds]

        mask_preds = F.interpolate(
            mask_preds.unsqueeze(0), size=upsampled_size,
            mode='bilinear')[:, :, :h, :w]
        masks = F.interpolate(
            mask_preds, size=ori_shape[:2], mode='bilinear').squeeze(0)
        masks = masks > cfg.mask_thr

        results.masks = masks
        results.labels = cls_labels
        results.scores = cls_scores

        return results


class DecoupledSOLOHead():
    pass
