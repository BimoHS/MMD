_base_ = [
    '../_base_/datasets/coco_detection.py', '../_base_/default_runtime.py'
]

norm_cfg = dict(type='SyncBN', requires_grad=True)
model = dict(
    type='RTDETR',
    num_queries=300,  # num_matching_queries, 900 for DINO
    with_box_refine=True,
    as_two_stage=True,
    data_preprocessor=dict(
        type='DetDataPreprocessor',
        mean=[0, 0, 0],
        std=[255, 255, 255],
        bgr_to_rgb=True,
        pad_size_divisor=32),  # 32 for DINO
    backbone=dict(
        type='ResNetV1d',
        depth=18,
        num_stages=4,
        out_indices=(1, 2, 3),
        frozen_stages=-1,
        norm_cfg=norm_cfg,
        norm_eval=False,  # True for DINO
        style='pytorch'),
    neck=dict(
        type='ChannelMapper',
        in_channels=[128, 256, 512],
        kernel_size=1,
        out_channels=256,
        act_cfg=None,
        norm_cfg=norm_cfg,
        num_outs=3),  # 4 for DINO
    encoder=dict(
        use_encoder_idx=[2],
        num_encoder_layers=1,
        in_channels=[256, 256, 256],
        out_channels=256,
        expansion=0.5,
        norm_cfg=norm_cfg,
        layer_cfg=dict(
            self_attn_cfg=dict(embed_dims=256, num_heads=8,
                               dropout=0.0),  # 0.1 for DeformDETR
            ffn_cfg=dict(
                embed_dims=256,
                feedforward_channels=1024,  # 2048 for DINO
                ffn_drop=0.0,
                act_cfg=dict(type='GELU')))),  # ReLU for DINO
    decoder=dict(
        num_layers=3,  # 6 for DINO
        layer_cfg=dict(
            self_attn_cfg=dict(embed_dims=256, num_heads=8,
                               dropout=0.0),  # 0.1 for DeformDETR
            cross_attn_cfg=dict(
                embed_dims=256,
                num_levels=3,  # 4 for DINO
                dropout=0.0),  # 0.1 for DeformDETR
            ffn_cfg=dict(
                embed_dims=256,
                feedforward_channels=1024,  # 2048 for DINO
                ffn_drop=0.0)),  # 0.1 for DeformDETR
        post_norm_cfg=None),
    positional_encoding=dict(
        num_feats=128,
        normalize=True,
        offset=0.0,  # -0.5 for DeformDETR
        temperature=20),  # 10000 for DeformDETR
    bbox_head=dict(
        type='RTDETRHead',
        num_classes=80,
        sync_cls_avg_factor=True,
        loss_cls=dict(
            type='RTDETRVarifocalLoss',  # FocalLoss in DINO
            use_sigmoid=True,
            gamma=2.0,
            alpha=0.75,  # 0.25 in DINO
            loss_weight=1.0),  # 2.0 in DeformDETR
        loss_bbox=dict(type='L1Loss', loss_weight=5.0),
        loss_iou=dict(type='GIoULoss', loss_weight=2.0)),
    dn_cfg=dict(  # TODO: Move to model.train_cfg ?
        label_noise_scale=0.5,
        box_noise_scale=1.0,  # 0.4 for DN-DETR
        group_cfg=dict(dynamic=True, num_groups=None,
                       num_dn_queries=100)),  # TODO: half num_dn_queries
    # training and testing settings
    train_cfg=dict(
        assigner=dict(
            type='HungarianAssigner',
            match_costs=[
                dict(type='FocalLossCost', weight=2.0),
                dict(type='BBoxL1Cost', weight=5.0, box_format='xywh'),
                dict(type='IoUCost', iou_mode='giou', weight=2.0)
            ])),
    test_cfg=dict(max_per_img=300))  # 100 for DeformDETR

# train_pipeline, NOTE the img_scale and the Pad's size_divisor is different
# from the default setting in mmdet.
train_pipeline = [
    dict(type='LoadImageFromFile', backend_args={{_base_.backend_args}}),
    dict(type='LoadAnnotations', with_bbox=True),
    dict(
        type='RandomApply',
        transforms=dict(type='PhotoMetricDistortion'),
        prob=0.8),
    dict(type='Expand', mean=[103.53, 116.28, 123.675]),
    dict(
        type='RandomApply',
        transforms=dict(type='MinIoURandomCrop', bbox_clip_border=False),
        prob=0.8),
    dict(type='RandomFlip', prob=0.5),
    dict(
        type='RandomChoiceResize',
        scales=[(480, 480), (512, 512), (544, 544), (576, 576), (608, 608),
                (640, 640), (640, 640), (640, 640), (672, 672), (704, 704),
                (736, 736), (768, 768), (800, 800)],
        random_interpolations=(
            'nearest', 'bilinear', 'bicubic', 'area', 'lanczos'),
        keep_ratio=False,
        clip_object_border=False),
    dict(type='FilterAnnotations', min_gt_bbox_wh=(1, 1), keep_empty=False),
    dict(type='PackDetInputs')
]

test_pipeline = [
    dict(type='LoadImageFromFile', backend_args={{_base_.backend_args}}),
    dict(
        type='Resize',
        scale=(640, 640),
        keep_ratio=False,
        interpolation='bicubic'),
    dict(type='LoadAnnotations', with_bbox=True),
    dict(
        type='PackDetInputs',
        meta_keys=('img_id', 'img_path', 'ori_shape', 'img_shape',
                   'scale_factor'))
]

train_dataloader = dict(
    batch_size=4,
    num_workers=4,
    batch_sampler=dict(drop_last=True),  # TODO remove
    dataset=dict(
        filter_cfg=dict(filter_empty_gt=False), pipeline=train_pipeline))
val_dataloader = dict(dataset=dict(pipeline=test_pipeline))
test_dataloader = dict(dataset=dict(pipeline=test_pipeline))

# set all layers in backbone to lr_mult=0.1
# set all norm layers, to decay_multi=0.0
num_blocks_list = (2, 2, 2, 2)  # r18
downsample_norm_idx_list = (2, 3, 3, 3)  # r18
backbone_norm_multi = dict(lr_mult=0.1, decay_mult=0.0)
custom_keys = {'backbone': dict(lr_mult=0.1, decay_mult=1.0)}
custom_keys.update({
    f'backbone.layer{stage_id + 1}.{block_id}.bn': backbone_norm_multi
    for stage_id, num_blocks in enumerate(num_blocks_list)
    for block_id in range(num_blocks)
})
custom_keys.update({
    f'backbone.layer{stage_id + 1}.{block_id}.downsample.{downsample_norm_idx - 1}': backbone_norm_multi   # noqa
    for stage_id, (num_blocks, downsample_norm_idx) in enumerate(zip(num_blocks_list, downsample_norm_idx_list))  # noqa
    for block_id in range(num_blocks)
})
# optimizer
optim_wrapper = dict(
    type='OptimWrapper',
    optimizer=dict(
        type='AdamW',
        lr=0.0001,  # 0.0002 for DeformDETR
        weight_decay=0.0001),
    clip_grad=dict(max_norm=0.1, norm_type=2),
    paramwise_cfg=dict(custom_keys=custom_keys, norm_decay_mult=0.0)
)  # custom_keys contains sampling_offsets and reference_points in DeformDETR  # noqa

# learning policy
max_epochs = 72
train_cfg = dict(
    type='EpochBasedTrainLoop', max_epochs=max_epochs, val_interval=1)

val_cfg = dict(type='ValLoop')
test_cfg = dict(type='TestLoop')

param_scheduler = [
    dict(
        type='LinearLR', start_factor=0.001, by_epoch=False, begin=0, end=2000)
]

# NOTE: `auto_scale_lr` is for automatically scaling LR,
# USER SHOULD NOT CHANGE ITS VALUES.
# base_batch_size = (4 GPUs) x (4 samples per GPU)
auto_scale_lr = dict(base_batch_size=16)

custom_hooks = [
    dict(
        type='EMAHook',
        ema_type='ExpMomentumEMA',
        momentum=0.0001,
        update_buffers=True,
        priority=49),
]
