_base_ = './fast_mask_rcnn_r50_fpn_1x.py'
model = dict(pretrained='torchvision://resnet101', backbone=dict(depth=101))
work_dir = './work_dirs/fast_mask_rcnn_r101_fpn_1x'
