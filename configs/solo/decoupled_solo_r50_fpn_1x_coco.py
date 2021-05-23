_base_ = [
    '../_base_/datasets/coco_instance.py', '../_base_/models/solo_r50_fpn.py',
    '../_base_/schedules/schedule_1x.py', '../_base_/default_runtime.py'
]
# model settings
model = dict(
    bbox_head=dict(
        type='DecoupledSOLOHead',
        num_classes=80,
        in_channels=256,
        stacked_convs=7,
        seg_feat_channels=256,
        strides=[8, 8, 16, 32, 32],
        scale_ranges=((1, 96), (48, 192), (96, 384), (192, 768), (384, 2048)),
        sigma=0.2,
        num_grids=[40, 36, 24, 16, 12],
        cate_down_pos=0,
        loss_mask=dict(type='DiceLoss', use_sigmoid=True, loss_weight=3.0),
        loss_cls=dict(
            type='FocalLoss',
            use_sigmoid=True,
            gamma=2.0,
            alpha=0.25,
            loss_weight=1.0),
        norm_cfg=dict(type='GN', num_groups=32, requires_grad=True)))

optimizer = dict(type='SGD', lr=0.01)
