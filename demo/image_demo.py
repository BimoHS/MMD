# Copyright (c) OpenMMLab. All rights reserved.
<<<<<<< HEAD
import asyncio
import json
import os
=======
"""Image Demo.

This script adopts a new infenence class, currently supports image path,
np.array and folder input formats, and will support video and webcam
in the future.

Example:
    Save visualizations and predictions results::

        python demo/image_demo.py demo/demo.jpg rtmdet-s

        python demo/image_demo.py demo/demo.jpg \
        configs/rtmdet/rtmdet_s_8xb32-300e_coco.py \
        --weights rtmdet_s_8xb32-300e_coco_20220905_161602-387a891e.pth

    Visualize prediction results::

        python demo/image_demo.py demo/demo.jpg rtmdet-ins-s --show

        python demo/image_demo.py demo/demo.jpg rtmdet-ins_s_8xb32-300e_coco \
        --show
"""

>>>>>>> mmdetection/main
from argparse import ArgumentParser

from mmengine.logging import print_log

from mmdet.apis import DetInferencer


def parse_args():
    parser = ArgumentParser()
<<<<<<< HEAD
    parser.add_argument('--img', help='图片路径', default=r"D:\data\beach_max\val\C3_S20220702140000_E20220702140959_320_max.jpg")
    parser.add_argument('--config', help='模型配置文件', default=r'D:\mmdetection\configs\solov2\solov2_r50_fpn_3x_coco.py')
    parser.add_argument('--checkpoint', help='模型路径',default=r"D:\mmdetection\tools\work_dirs\solov2_r50_fpn_3x_coco\epoch_36.pth")
    parser.add_argument('--out-file', default=r'D:\data\000003.jpg', help='图片输出路径')
=======
    parser.add_argument(
        'inputs', type=str, help='Input image file or folder path.')
    parser.add_argument(
        'model',
        type=str,
        help='Config or checkpoint .pth file or the model name '
        'and alias defined in metafile. The model configuration '
        'file will try to read from .pth if the parameter is '
        'a .pth weights file.')
    parser.add_argument('--weights', default=None, help='Checkpoint file')
    parser.add_argument(
        '--out-dir',
        type=str,
        default='outputs',
        help='Output directory of images or prediction results.')
>>>>>>> mmdetection/main
    parser.add_argument(
        '--device', default='cpu', help='用于推理的设备')
    parser.add_argument(
<<<<<<< HEAD
        '--palette',
        default='coco',
        choices=['coco', 'voc', 'citys', 'random'],
        help='用于可视化检测结果的调色板,每个数据集都有它默认的调色板.或者也可以随机生成')
    parser.add_argument(
        '--score-thr', type=float, default=0.3, help='box置信度阈值')
=======
        '--pred-score-thr',
        type=float,
        default=0.3,
        help='bbox score threshold')
    parser.add_argument(
        '--batch-size', type=int, default=1, help='Inference batch size.')
>>>>>>> mmdetection/main
    parser.add_argument(
        '--show',
        action='store_true',
<<<<<<< HEAD
        help='是否为异步推理设置异步选项.')
    args = parser.parse_args()
    return args


def main(args):
    model = init_detector(args.config, args.checkpoint, device=args.device)
    # load_names = json.load(open(r'D:\data\beach_max\annotations\val.json','r'))
    # for img_info in load_names['images']:
    #     args.img = r'D:\data\beach_max\images' + os.sep + img_info['file_name']
    #     args.out_file = r'D:\data\beach_max\val_out' + os.sep + img_info['file_name']
    # 从配置文件和权重文件构建模型
    # 测试单张图片  理论上是支持多张图片一起推理,但是后处理阶段仅仅支持img为numpy型数据或单个路径
    result = inference_detector(model, args.img)
    # 显示结果
    show_result_pyplot(
        model,
        args.img,
        result,
        palette=args.palette,
        score_thr=args.score_thr,
        out_file=args.out_file)
=======
        help='Display the image in a popup window.')
    parser.add_argument(
        '--no-save-vis',
        action='store_true',
        help='Do not save detection vis results')
    parser.add_argument(
        '--no-save-pred',
        action='store_true',
        help='Do not save detection json results')
    parser.add_argument(
        '--print-result',
        action='store_true',
        help='Whether to print the results.')
    parser.add_argument(
        '--palette',
        default='none',
        choices=['coco', 'voc', 'citys', 'random', 'none'],
        help='Color palette used for visualization')

    call_args = vars(parser.parse_args())

    if call_args['no_save_vis'] and call_args['no_save_pred']:
        call_args['out_dir'] = ''

    if call_args['model'].endswith('.pth'):
        print_log('The model is a weight file, automatically '
                  'assign the model to --weights')
        call_args['weights'] = call_args['model']
        call_args['model'] = None

    init_kws = ['model', 'weights', 'device', 'palette']
    init_args = {}
    for init_kw in init_kws:
        init_args[init_kw] = call_args.pop(init_kw)

    return init_args, call_args


def main():
    init_args, call_args = parse_args()
    # TODO: Video and Webcam are currently not supported and
    #  may consume too much memory if your input folder has a lot of images.
    #  We will be optimized later.
    inferencer = DetInferencer(**init_args)
    inferencer(**call_args)
>>>>>>> mmdetection/main

    if call_args['out_dir'] != '' and not (call_args['no_save_vis']
                                           and call_args['no_save_pred']):
        print_log(f'results have been saved at {call_args["out_dir"]}')


if __name__ == '__main__':
    main()
