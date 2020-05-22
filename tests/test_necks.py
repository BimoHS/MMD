import pytest
import torch
from torch.nn.modules.batchnorm import _BatchNorm

from mmdet.models.necks import FPN


def test_fpn():
    """Tests fpn """

    in_channels = [8, 16, 32, 64]
    feat_sizes = [64, 32, 16, 8]
    out_channels = 8
    # `num_outs` is not equal to len(in_channels) - start_level
    with pytest.raises(AssertionError):
        FPN(in_channels=in_channels,
            out_channels=out_channels,
            start_level=1,
            num_outs=2)

    # `end_level` is larger than len(in_channels) - 1
    with pytest.raises(AssertionError):
        FPN(in_channels=in_channels,
            out_channels=out_channels,
            start_level=1,
            end_level=4,
            num_outs=2)

    # `num_outs` is not equal to end_level - start_level
    with pytest.raises(AssertionError):
        FPN(in_channels=in_channels,
            out_channels=out_channels,
            start_level=1,
            end_level=3,
            num_outs=1)

    fpn_model = FPN(
        in_channels=in_channels,
        out_channels=out_channels,
        start_level=1,
        add_extra_convs=True,
        num_outs=5)

    # FPN expects a multiple levels of features per image
    feats = [
        torch.rand(1, in_channels[i], feat_sizes[i], feat_sizes[i])
        for i in range(len(in_channels))
    ]
    outs = fpn_model(feats)
    assert len(outs) == fpn_model.num_outs
    for i in range(len(in_channels)):
        # feat size of input and output should be the same
        if i >= fpn_model.start_level:
            feats[i].shape[2:] == outs[i].shape[2:]

    # Tests for fpn with no extra convs (pooling is used instead)
    fpn_model = FPN(
        in_channels=in_channels,
        out_channels=out_channels,
        start_level=1,
        add_extra_convs=False,
        num_outs=5)
    fpn_model(feats)

    # Tests for fpn with lateral bns
    fpn_model = FPN(
        in_channels=in_channels,
        out_channels=out_channels,
        start_level=1,
        add_extra_convs=True,
        no_norm_on_lateral=False,
        norm_cfg=dict(type='BN', requires_grad=True),
        num_outs=5)
    fpn_model(feats)
    bn_exist = False
    for m in fpn_model.modules():
        if isinstance(m, _BatchNorm):
            bn_exist = True
    assert bn_exist

    # Bilinear upsample
    fpn_bilinear_upsample = FPN(
        in_channels=in_channels,
        out_channels=out_channels,
        start_level=1,
        add_extra_convs=True,
        upsample_cfg=dict(mode='bilinear', align_corners=True),
        num_outs=5)
    fpn_bilinear_upsample(feats)

    # Scale factor instead of fixed upsample size upsample
    scale_factor_upsample = FPN(
        in_channels=in_channels,
        out_channels=out_channels,
        start_level=1,
        add_extra_convs=True,
        upsample_cfg=dict(scale_factor=2),
        num_outs=5)
    scale_factor_upsample(feats)

    # Extra convs source is 'inputs'
    fpn_model = FPN(
        in_channels=in_channels,
        out_channels=out_channels,
        start_level=1,
        num_outs=5,
        extra_convs_source='inputs')
    assert fpn_model.extra_convs_source == 'inputs'
    fpn_model(feats)

    # Extra convs source is 'laterals'
    fpn_model = FPN(
        in_channels=in_channels,
        out_channels=out_channels,
        start_level=1,
        num_outs=5,
        extra_convs_source='laterals')
    assert fpn_model.extra_convs_source == 'laterals'
    fpn_model(feats)

    # Extra convs source is 'outputs'
    fpn_model = FPN(
        in_channels=in_channels,
        out_channels=out_channels,
        start_level=1,
        num_outs=5,
        extra_convs_source='outputs')
    assert fpn_model.extra_convs_source == 'outputs'
    fpn_model(feats)

    # extra_convs_on_inputs=False is equal to `extra convs source` is 'outputs'
    fpn_model = FPN(
        in_channels=in_channels,
        out_channels=out_channels,
        extra_convs_on_inputs=False,
        start_level=1,
        num_outs=5,
    )
    assert fpn_model.extra_convs_source == 'outputs'
    fpn_model(feats)
