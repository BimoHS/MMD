_base_ = ['faster_rcnn_r50_fpn_1x_openimages.py']

model = dict(
    roi_head=dict(bbox_head=dict(num_classes=500)),
    test_cfg=dict(rcnn=dict(score_thr=0.01)))

# dataset settings
dataset_type = 'OpenImagesChallengeDataset'
data_root = 'data/OpenImages/'
data = dict(
    train=dict(
        type=dataset_type,
        ann_file=data_root + 'challenge2019/train_small.txt',
        img_prefix=data_root + 'OpenImages/',
        label_description_file=data_root +
        'challenge2019/cls-label-description.csv',
        hierarchy_file=data_root + 'challenge2019/class_label_tree.np'),
    val=dict(
        type=dataset_type,
        ann_file=data_root + 'challenge2019/train_small.txt',
        img_prefix=data_root + 'OpenImages/',
        label_description_file=data_root +
        'challenge2019/cls-label-description.csv',
        hierarchy_file=data_root + 'challenge2019/class_label_tree.np',
        meta_file=data_root +
        'challenge2019/challenge-2019-validation-metas-small.pkl'),
    test=dict(
        type=dataset_type,
        ann_file=data_root + 'challenge2019/train_small.txt',
        img_prefix=data_root + 'OpenImages/',
        label_description_file=data_root +
        'challenge2019/cls-label-description.csv',
        hierarchy_file=data_root + 'challenge2019/class_label_tree.np',
        meta_file=data_root +
        'challenge2019/challenge-2019-validation-metas-small.pkl'))
evaluation = dict(interval=1, metric='mAP')
