# Copyright (c) OpenMMLab. All rights reserved.
import time
import unittest
from unittest import TestCase

import torch
from mmengine.logging import MessageHub
from parameterized import parameterized

from mmdet.core import DetDataSample
from mmdet.testing import demo_mm_inputs, get_detector_cfg
from mmdet.utils import register_all_modules

register_all_modules()


class TestSingleStageDetector(TestCase):

    @parameterized.expand([
        'retinanet/retinanet_r18_fpn_1x_coco.py',
        'centernet/centernet_resnet18_140e_coco.py',
        # 'fsaf/fsaf_r50_fpn_1x_coco.py',
        'yolox/yolox_tiny_8x8_300e_coco.py',
        # 'yolo/yolov3_mobilenetv2_320_300e_coco.py'
    ])
    def test_init(self, cfg_file):
        model = get_detector_cfg(cfg_file)
        model.backbone.init_cfg = None

        from mmdet.models import build_detector
        detector = build_detector(model)
        assert detector.backbone
        assert detector.neck
        assert detector.bbox_head

    @parameterized.expand([
        ('retinanet/retinanet_r18_fpn_1x_coco.py', ('cpu', 'cuda')),
        ('centernet/centernet_resnet18_140e_coco.py', ('cpu', 'cuda')),
        # ('fsaf/fsaf_r50_fpn_1x_coco.py', ('cpu', 'cuda')),
        ('yolox/yolox_tiny_8x8_300e_coco.py', ('cpu', 'cuda')),
        # ('yolo/yolov3_mobilenetv2_320_300e_coco.py', ('cpu', 'cuda'))
    ])
    def test_single_stage_forward_loss_mode(self, cfg_file, devices):
        message_hub = MessageHub.get_instance(
            f'test_single_stage_forward_loss_mode-{time.time()}')
        message_hub.update_info('iter', 0)
        message_hub.update_info('epoch', 0)
        model = get_detector_cfg(cfg_file)
        model.backbone.init_cfg = None

        from mmdet.models import build_detector
        assert all([device in ['cpu', 'cuda'] for device in devices])

        for device in devices:
            detector = build_detector(model)
            detector.init_weights()

            if device == 'cuda':
                if not torch.cuda.is_available():
                    return unittest.skip('test requires GPU and torch+cuda')
                detector = detector.cuda()

            packed_inputs = demo_mm_inputs(2, [[3, 128, 128], [3, 125, 130]])
            batch_inputs, data_samples = detector.data_preprocessor(
                packed_inputs, True)
            losses = detector.forward(batch_inputs, data_samples, mode='loss')
            assert isinstance(losses, dict)

    @parameterized.expand([
        ('retinanet/retinanet_r18_fpn_1x_coco.py', ('cpu', 'cuda')),
        ('centernet/centernet_resnet18_140e_coco.py', ('cpu', 'cuda')),
        # ('fsaf/fsaf_r50_fpn_1x_coco.py', ('cpu', 'cuda')),
        ('yolox/yolox_tiny_8x8_300e_coco.py', ('cpu', 'cuda')),
        # ('yolo/yolov3_mobilenetv2_320_300e_coco.py', ('cpu', 'cuda'))
    ])
    def test_single_stage_forward_predict_mode(self, cfg_file, devices):
        model = get_detector_cfg(cfg_file)
        model.backbone.init_cfg = None

        from mmdet.models import build_detector
        assert all([device in ['cpu', 'cuda'] for device in devices])

        for device in devices:
            detector = build_detector(model)

            if device == 'cuda':
                if not torch.cuda.is_available():
                    return unittest.skip('test requires GPU and torch+cuda')
                detector = detector.cuda()

            packed_inputs = demo_mm_inputs(2, [[3, 128, 128], [3, 125, 130]])
            batch_inputs, data_samples = detector.data_preprocessor(
                packed_inputs, False)
            # Test forward test
            detector.eval()
            with torch.no_grad():
                batch_results = detector.forward(
                    batch_inputs, data_samples, mode='predict')
                assert len(batch_results) == 2
                assert isinstance(batch_results[0], DetDataSample)

    @parameterized.expand([
        ('retinanet/retinanet_r18_fpn_1x_coco.py', ('cpu', 'cuda')),
        ('centernet/centernet_resnet18_140e_coco.py', ('cpu', 'cuda')),
        # ('fsaf/fsaf_r50_fpn_1x_coco.py', ('cpu', 'cuda')),
        ('yolox/yolox_tiny_8x8_300e_coco.py', ('cpu', 'cuda')),
        # ('yolo/yolov3_mobilenetv2_320_300e_coco.py', ('cpu', 'cuda'))
    ])
    def test_single_stage_forward_tensor_mode(self, cfg_file, devices):
        model = get_detector_cfg(cfg_file)
        model.backbone.init_cfg = None

        from mmdet.models import build_detector
        assert all([device in ['cpu', 'cuda'] for device in devices])

        for device in devices:
            detector = build_detector(model)

            if device == 'cuda':
                if not torch.cuda.is_available():
                    return unittest.skip('test requires GPU and torch+cuda')
                detector = detector.cuda()

            packed_inputs = demo_mm_inputs(2, [[3, 128, 128], [3, 125, 130]])
            batch_inputs, data_samples = detector.data_preprocessor(
                packed_inputs, False)
            batch_results = detector.forward(
                batch_inputs, data_samples, mode='tensor')
            assert isinstance(batch_results, tuple)
