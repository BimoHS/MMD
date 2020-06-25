_base_ = './faster_rcnn_s50_fpn_syncbn-all_mstrain-range_1x_coco.py'
model = dict(
    pretrained='pretrain_model/resnest101_d2-9cb052ad.pth',
    backbone=dict(stem_channels=128, depth=101))
