import torch
import torch.nn as nn
import torch.nn.functional as F
from mmcv.cnn import (ConvModule, DepthwiseSeparableConvModule,
                      bias_init_with_prob, constant_init, is_norm, normal_init)
from mmcv.ops.nms import batched_nms

from mmdet.core import MlvlPointGenerator, multi_apply
from mmdet.core.bbox.assigners.sim_ota_assigner import SimOTAAssigner
from mmdet.core.bbox.transforms import bbox_xyxy_to_cxcywh
from ..builder import HEADS, build_loss
from .base_dense_head import BaseDenseHead
from .dense_test_mixins import BBoxTestMixin


@HEADS.register_module()
class YOLOXHead(BaseDenseHead, BBoxTestMixin):
    """YOLOXHead head used in `YOLOX <https://arxiv.org/abs/2107.08430>`_.

    Args:
        num_classes (int): Number of categories excluding the background
            category.
        in_channels (int): Number of channels in the input feature map.
        feat_channels (int): Number of hidden channels in stacking convs.
            Default: 256
        stacked_convs (int): Number of stacking convs of the head.
            Default: 2.
        strides (tuple): Downsample factor of each feature map.
        use_depthwise (bool): Whether to depthwise separable convolution in
            blocks. Default: False
        dcn_on_last_conv (bool): If true, use dcn in the last layer of
            towers. Default: False.
        conv_bias (bool | str): If specified as `auto`, it will be decided by
            the norm_cfg. Bias of conv will be set as True if `norm_cfg` is
            None, otherwise False. Default: "auto".
        conv_cfg (dict): Config dict for convolution layer. Default: None.
        norm_cfg (dict): Config dict for normalization layer. Default: None.
        act_cfg (dict): Config dict for activation layer. Default: None.
        loss_cls (dict): Config of classification loss.
        loss_bbox (dict): Config of localization loss.
        loss_obj (dict): Config of objectness loss.
        train_cfg (dict): Training config of anchor head.
        test_cfg (dict): Testing config of anchor head.
        init_cfg (dict or list[dict], optional): Initialization config dict.
    """

    def __init__(self,
                 num_classes,
                 in_channels,
                 feat_channels=256,
                 stacked_convs=2,
                 strides=[8, 16, 32],
                 use_depthwise=False,
                 dcn_on_last_conv=False,
                 conv_bias='auto',
                 conv_cfg=None,
                 norm_cfg=dict(type='BN', momentum=0.03, eps=0.001),
                 act_cfg=dict(type='Swish'),
                 loss_cls=dict(
                     type='CrossEntropyLoss',
                     use_sigmoid=True,
                     reduction='sum',
                     loss_weight=1.0),
                 loss_bbox=dict(
                     type='YOLOXIoULoss',
                     eps=1e-16,
                     reduction='sum',
                     loss_weight=5.0),
                 loss_obj=dict(
                     type='CrossEntropyLoss',
                     use_sigmoid=True,
                     reduction='sum',
                     loss_weight=1.0),
                 loss_l1=dict(type='L1Loss', reduction='sum', loss_weight=1.0),
                 train_cfg=None,
                 test_cfg=None,
                 init_cfg=None):

        super().__init__(init_cfg=init_cfg)
        self.num_classes = num_classes
        self.cls_out_channels = num_classes
        self.in_channels = in_channels
        self.feat_channels = feat_channels
        self.stacked_convs = stacked_convs
        self.strides = strides
        self.use_depthwise = use_depthwise
        self.dcn_on_last_conv = dcn_on_last_conv
        assert conv_bias == 'auto' or isinstance(conv_bias, bool)
        self.conv_bias = conv_bias
        self.use_sigmoid_cls = True

        self.conv_cfg = conv_cfg
        self.norm_cfg = norm_cfg
        self.act_cfg = act_cfg

        self.loss_cls = build_loss(loss_cls)
        self.loss_bbox = build_loss(loss_bbox)
        self.loss_obj = build_loss(loss_obj)

        self.use_l1 = False
        self.loss_l1 = build_loss(loss_l1)

        self.prior_generator = MlvlPointGenerator(strides, offset=0)

        self.test_cfg = test_cfg
        self.train_cfg = train_cfg

        self.assigner = SimOTAAssigner(num_classes)

        self._init_layers()

    def _init_layers(self):
        self.multi_level_cls_convs = nn.ModuleList()
        self.multi_level_reg_convs = nn.ModuleList()
        self.multi_level_conv_cls = nn.ModuleList()
        self.multi_level_conv_reg = nn.ModuleList()
        self.multi_level_conv_obj = nn.ModuleList()
        for _ in self.strides:
            self.multi_level_cls_convs.append(self._build_stacked_convs())
            self.multi_level_reg_convs.append(self._build_stacked_convs())
            conv_cls, conv_reg, conv_obj = self._build_predictor()
            self.multi_level_conv_cls.append(conv_cls)
            self.multi_level_conv_reg.append(conv_reg)
            self.multi_level_conv_obj.append(conv_obj)

    def _build_stacked_convs(self):
        """Initialize conv layers of a single level head."""
        conv = DepthwiseSeparableConvModule \
            if self.use_depthwise else ConvModule
        stacked_convs = []
        for i in range(self.stacked_convs):
            chn = self.in_channels if i == 0 else self.feat_channels
            if self.dcn_on_last_conv and i == self.stacked_convs - 1:
                conv_cfg = dict(type='DCNv2')
            else:
                conv_cfg = self.conv_cfg
            stacked_convs.append(
                conv(
                    chn,
                    self.feat_channels,
                    3,
                    stride=1,
                    padding=1,
                    conv_cfg=conv_cfg,
                    norm_cfg=self.norm_cfg,
                    act_cfg=self.act_cfg,
                    bias=self.conv_bias))
        return nn.Sequential(*stacked_convs)

    def _build_predictor(self):
        """Initialize predictor layers of a single level head."""
        conv_cls = nn.Conv2d(self.feat_channels, self.cls_out_channels, 1)
        conv_reg = nn.Conv2d(self.feat_channels, 4, 1)
        conv_obj = nn.Conv2d(self.feat_channels, 1, 1)
        return conv_cls, conv_reg, conv_obj

    def init_weights(self):
        for m in self.modules():
            if isinstance(m, nn.Conv2d):
                normal_init(m, mean=0, std=0.01)
            if is_norm(m):
                constant_init(m, 1)

        # Use prior in model initialization to improve stability
        bias_init = bias_init_with_prob(0.01)
        for conv_cls, conv_obj in zip(self.multi_level_conv_cls,
                                      self.multi_level_conv_obj):
            conv_cls.bias.data.fill_(bias_init)
            conv_obj.bias.data.fill_(bias_init)

    def forward_single(self, x, cls_convs, reg_convs, conv_cls, conv_reg,
                       conv_obj):
        """Forward feature of a single scale level.

        Args:
            x (Tensor): Features of a single scale level.

        Returns:
            tuple:
                cls_score (Tensor): Cls scores for a single scale level
                    the channels number is num_classes.
                bbox_pred (Tensor): Box energies / deltas for a single scale
                    level, the channels number is 4.
                objectness (Tensor): Objectness for a single scale level,
                    the channels number is 1.
        """
        cls_feat = cls_convs(x)
        reg_feat = reg_convs(x)

        cls_score = conv_cls(cls_feat)
        bbox_pred = conv_reg(reg_feat)
        objectness = conv_obj(reg_feat)

        return cls_score, bbox_pred, objectness

    def forward(self, feats):
        """Forward features from the upstream network.

        Args:
            feats (tuple[Tensor]): Features from the upstream network, each is
                a 4D-tensor.

        Returns:
            tuple[Tensor]: A tuple of multi-level predication map, each is a
                4D-tensor of shape (batch_size, 5+num_classes, height, width).
        """

        return multi_apply(self.forward_single, feats,
                           self.multi_level_cls_convs,
                           self.multi_level_reg_convs,
                           self.multi_level_conv_cls,
                           self.multi_level_conv_reg,
                           self.multi_level_conv_obj)

    def get_bboxes(self,
                   cls_scores,
                   bbox_preds,
                   objectnesses,
                   img_metas=None,
                   cfg=None,
                   rescale=False,
                   with_nms=True):
        """Transform network outputs of a batch into bbox results.
        Note: When score_factors is not None, the cls_scores are
        usually multiplied by it then obtain the real score used in NMS,
        such as CenterNess in FCOS, IoU branch in ATSS.
        Args:
            cls_scores (list[Tensor]): Classification scores for all
                scale levels, each is a 4D-tensor, has shape
                (batch_size, num_priors * num_classes, H, W).
            bbox_preds (list[Tensor]): Box energies / deltas for all
                scale levels, each is a 4D-tensor, has shape
                (batch_size, num_priors * 4, H, W).
            objectnesses (list[Tensor], Optional): Score factor for
                all scale level, each is a 4D-tensor, has shape
                (batch_size, num_priors * 1, H, W). Default None.
            img_metas (list[dict], Optional): Image meta info. Default None.
            cfg (mmcv.Config, Optional): Test / postprocessing configuration,
                if None, test_cfg would be used.  Default None.
            rescale (bool): If True, return boxes in original image space.
                Default False.
            with_nms (bool): If True, do nms before return boxes.
                Default True.
        Returns:
            list[list[Tensor, Tensor]]: Each item in result_list is 2-tuple.
                The first item is an (n, 5) tensor, where the first 4 columns
                are bounding box positions (tl_x, tl_y, br_x, br_y) and the
                5-th column is a score between 0 and 1. The second item is a
                (n,) tensor where each item is the predicted class label of
                the corresponding box.
        """
        assert len(cls_scores) == len(bbox_preds) == len(objectnesses)
        cfg = self.test_cfg if cfg is None else cfg
        scale_factors = [img_meta['scale_factor'] for img_meta in img_metas]

        num_imgs = len(img_metas)
        featmap_sizes = [cls_score.shape[2:] for cls_score in cls_scores]
        mlvl_priors = self.prior_generator.grid_priors(
            featmap_sizes, cls_scores[0].device, with_stride=True)

        # flatten cls_scores, bbox_preds and objectness
        flatten_cls_scores = [
            cls_score.permute(0, 2, 3, 1).reshape(num_imgs, -1,
                                                  self.cls_out_channels)
            for cls_score in cls_scores
        ]
        flatten_bbox_preds = [
            bbox_pred.permute(0, 2, 3, 1).reshape(num_imgs, -1, 4)
            for bbox_pred in bbox_preds
        ]
        flatten_objectness = [
            objectness.permute(0, 2, 3, 1).reshape(num_imgs, -1)
            for objectness in objectnesses
        ]

        flatten_cls_scores = torch.cat(flatten_cls_scores, dim=1).sigmoid()
        flatten_bbox_preds = torch.cat(flatten_bbox_preds, dim=1)
        flatten_objectness = torch.cat(flatten_objectness, dim=1).sigmoid()
        flatten_priors = torch.cat(mlvl_priors)

        flatten_bboxes = self._bbox_decode(flatten_priors, flatten_bbox_preds)

        if rescale:
            flatten_bboxes[..., :4] /= flatten_bboxes.new_tensor(scale_factors)

        result_list = []
        for img_id in range(len(img_metas)):
            cls_scores = flatten_cls_scores[img_id]
            score_factor = flatten_objectness[img_id]
            bboxes = flatten_bboxes[img_id]

            result_list.append(
                self._bboxes_nms(cls_scores, bboxes, score_factor, cfg))

        return result_list

    def _bbox_decode(self, priors, bbox_preds):
        xys = (bbox_preds[..., :2] * priors[:, 2:]) + priors[:, :2]
        whs = bbox_preds[..., 2:].exp() * priors[:, 2:]

        tl_x = (xys[..., 0] - whs[..., 0] / 2)
        tl_y = (xys[..., 1] - whs[..., 1] / 2)
        br_x = (xys[..., 0] + whs[..., 0] / 2)
        br_y = (xys[..., 1] + whs[..., 1] / 2)

        decoded_bboxes = torch.stack([tl_x, tl_y, br_x, br_y], -1)
        return decoded_bboxes

    def _bboxes_nms(self, cls_scores, bboxes, score_factor, cfg):
        max_scores, labels = torch.max(cls_scores, 1)
        valid_mask = score_factor * max_scores >= cfg.score_thr

        bboxes = bboxes[valid_mask]
        scores = max_scores[valid_mask] * score_factor[valid_mask]
        labels = labels[valid_mask]

        if labels.numel() == 0:
            return bboxes, labels
        else:
            dets, keep = batched_nms(bboxes, scores, labels, cfg.nms)
            return dets, labels[keep]

    def loss(self,
             cls_scores,
             bbox_preds,
             objectnesses,
             gt_bboxes,
             gt_labels,
             img_metas,
             gt_bboxes_ignore=None,
             imgs=None):

        num_imgs = len(img_metas)
        featmap_sizes = [cls_score.shape[2:] for cls_score in cls_scores]
        mlvl_priors = self.prior_generator.grid_priors(
            featmap_sizes, cls_scores[0].device, with_stride=True)

        # flatten cls_scores, bbox_preds and objectness
        flatten_cls_scores = [
            cls_score.permute(0, 2, 3, 1).reshape(num_imgs, -1,
                                                  self.cls_out_channels)
            for cls_score in cls_scores
        ]
        flatten_bbox_preds = [
            bbox_pred.permute(0, 2, 3, 1).reshape(num_imgs, -1, 4)
            for bbox_pred in bbox_preds
        ]
        flatten_objectness = [
            objectness.permute(0, 2, 3, 1).reshape(num_imgs, -1)
            for objectness in objectnesses
        ]

        flatten_cls_scores = torch.cat(flatten_cls_scores, dim=1)
        flatten_bbox_preds = torch.cat(flatten_bbox_preds, dim=1)
        flatten_objectness = torch.cat(flatten_objectness, dim=1)
        flatten_priors = torch.cat(mlvl_priors)

        flatten_bboxes = self._bbox_decode(flatten_priors, flatten_bbox_preds)

        num_total_samples = 0
        foreground_masks = []
        cls_targets = []
        obj_targets = []
        bbox_targets = []
        l1_targets = []

        for idx in range(num_imgs):
            (foreground_mask, cls_target, obj_target, bbox_target, l1_target,
             num_fg_img) = self._get_target_single(
                 flatten_cls_scores[idx].detach(),
                 flatten_objectness[idx].detach(), flatten_priors,
                 flatten_bboxes[idx].detach(), gt_bboxes[idx], gt_labels[idx])

            foreground_masks.append(foreground_mask)
            cls_targets.append(cls_target)
            obj_targets.append(obj_target.to(flatten_objectness.dtype))
            bbox_targets.append(bbox_target)
            l1_targets.append(l1_target)
            num_total_samples += num_fg_img

        foreground_masks = torch.cat(foreground_masks, 0)
        cls_targets = torch.cat(cls_targets, 0)
        obj_targets = torch.cat(obj_targets, 0)
        bbox_targets = torch.cat(bbox_targets, 0)
        if self.use_l1:
            l1_targets = torch.cat(l1_targets, 0)
        num_total_samples = max(num_total_samples, 1)

        loss_iou = self.loss_bbox(
            flatten_bboxes.view(-1, 4)[foreground_masks],
            bbox_targets) / num_total_samples
        loss_obj = self.loss_obj(flatten_objectness.view(-1, 1),
                                 obj_targets) / num_total_samples
        loss_cls = self.loss_cls(
            flatten_cls_scores.view(-1, self.num_classes)[foreground_masks],
            cls_targets) / num_total_samples

        if self.use_l1:
            loss_l1 = self.loss_l1(
                flatten_bbox_preds.view(-1, 4)[foreground_masks],
                l1_targets) / num_total_samples

            return dict(
                loss_cls=loss_cls,
                loss_iou=loss_iou,
                loss_obj=loss_obj,
                loss_l1=loss_l1)
        else:
            return dict(
                loss_cls=loss_cls, loss_iou=loss_iou, loss_obj=loss_obj)

    @torch.no_grad()
    def _get_target_single(self,
                           cls_scores,
                           objectness,
                           priors,
                           decoded_bboxes,
                           gt_bboxes,
                           gt_labels,
                           eps=1e-8):
        num_priors = priors.size(0)
        num_gts = gt_labels.size(0)
        # No target
        if num_gts == 0:
            cls_target = cls_scores.new_zeros((0, self.num_classes))
            bbox_target = cls_scores.new_zeros((0, 4))
            l1_target = cls_scores.new_zeros((0, 4))
            obj_target = cls_scores.new_zeros((num_priors, 1))
            fg_mask = cls_scores.new_zeros(num_priors).bool()
            return fg_mask, cls_target, obj_target, bbox_target, l1_target, 0

        # YOLOX uses center priors with 0.5 offset to assign targets,
        # but use center priors without offset to regress bboxes.
        offset_priors = torch.cat(
            [priors[:, :2] + priors[:, 2:] * 0.5, priors[:, 2:]], dim=-1)

        (gt_matched_classes, fg_mask, pred_ious_this_matching, matched_gt_inds,
         num_fg_per_img) = self.assigner.assign(
             cls_scores.sigmoid() * objectness.unsqueeze(1).sigmoid(),
             offset_priors, decoded_bboxes, gt_bboxes, gt_labels)

        # iou aware classification score
        cls_target = F.one_hot(
            gt_matched_classes.to(torch.int64),
            self.num_classes) * pred_ious_this_matching.unsqueeze(-1)
        obj_target = fg_mask.unsqueeze(-1)
        bbox_target = gt_bboxes[matched_gt_inds]
        l1_target = cls_scores.new_zeros((num_fg_per_img, 4))
        if self.use_l1:
            l1_target = self._get_l1_target(l1_target,
                                            gt_bboxes[matched_gt_inds],
                                            priors[fg_mask], eps)

        return (fg_mask, cls_target, obj_target, bbox_target, l1_target,
                num_fg_per_img)

    def _get_l1_target(self, l1_target, gt_bboxes, priors, eps=1e-8):
        """Convert gt bboxes to center offset and log width height."""
        gt_cxcywh = bbox_xyxy_to_cxcywh(gt_bboxes)
        l1_target[:, 0] = (gt_cxcywh[:, 0] - priors[:, 0]) / priors[:, 2]
        l1_target[:, 1] = (gt_cxcywh[:, 1] - priors[:, 1]) / priors[:, 3]
        l1_target[:, 2] = torch.log(gt_cxcywh[:, 2] / priors[:, 2] + eps)
        l1_target[:, 3] = torch.log(gt_cxcywh[:, 3] / priors[:, 3] + eps)
        return l1_target
