_base_ = './cascade_rcnn_r50_fpn_1x.py'
model = dict(
    pretrained='torchvision://resnet101',
    backbone=dict(
        type='ResNet',
        depth=101,
        num_stages=4,
        out_indices=(0, 1, 2, 3),
        frozen_stages=1,
        style='pytorch'))
work_dir = './work_dirs/cascade_rcnn_r101_fpn_1x'
