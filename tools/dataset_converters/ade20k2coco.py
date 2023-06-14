import argparse
import json
import os
from pathlib import Path

import numpy as np
import pycocotools.mask as mask_util
from mmengine.utils import ProgressBar, mkdir_or_exist
from panopticapi.utils import IdGenerator, save_json
from PIL import Image

from mmdet.datasets.ade20k import ADE20KPanopticDataset


def parse_args():
    parser = argparse.ArgumentParser(
        description='Convert ADE20K annotations to COCO format')
    parser.add_argument('src', help='ade20k data path')
    parser.add_argument('--task', help='task name', default='panoptic')
    args = parser.parse_args()
    return args


def prepare_instance_annotations(dataset_dir: str):
    dataset_dir = Path(dataset_dir)
    for name, dirname in [('train', 'training'), ('val', 'validation')]:
        image_dir = dataset_dir / 'images' / dirname
        instance_dir = dataset_dir / 'annotations_instance' / dirname

        ann_id = 0

        # json
        out_file = dataset_dir / f'ade20k_instance_{name}.json'

        # json config
        instance_config_file = dataset_dir / 'imgCatIds.json'
        with open(instance_config_file, 'r') as f:
            category_dict = json.load(f)['categories']

        # catid mapping
        mapping_file = dataset_dir / 'categoryMapping.txt'
        with open(mapping_file, 'r') as f:
            map_id = {}
            for i, line in enumerate(f.readlines()):
                if i == 0:
                    continue
                ins_id, sem_id, _ = line.strip().split()
                map_id[int(ins_id)] = int(sem_id) - 1

        for cat in category_dict:
            cat['id'] = map_id[cat['id']]

        filenames = sorted(list(image_dir.iterdir()))

        ann_dict = {}
        images = []
        annotations = []

        progressbar = ProgressBar(len(filenames))
        for filename in filenames:
            image = {}
            image_id = filename.stem

            image['id'] = image_id
            image['file_name'] = filename.name

            original_format = np.array(Image.open(filename))
            image['height'] = original_format.shape[0]
            image['width'] = original_format.shape[1]

            images.append(image)

            instance_file = instance_dir / f'{image_id}.png'
            ins_seg = np.array(Image.open(instance_file))
            assert ins_seg.dtype == np.uint8

            instance_cat_ids = ins_seg[..., 0]
            instance_ins_ids = ins_seg[..., 1]

            for thing_id in np.unique(instance_ins_ids):
                if thing_id == 0:
                    continue
                mask = instance_ins_ids == thing_id
                instance_cat_id = np.unique(instance_cat_ids[mask])
                assert len(instance_cat_id) == 1

                anno = {}
                anno['id'] = ann_id
                ann_id += 1
                anno['image_id'] = image['id']
                anno['iscrowd'] = int(0)
                anno['category_id'] = int(map_id[instance_cat_id[0]])

                inds = np.nonzero(mask)
                ymin, ymax = inds[0].min(), inds[0].max()
                xmin, xmax = inds[1].min(), inds[1].max()
                anno['bbox'] = [
                    int(xmin),
                    int(ymin),
                    int(xmax - xmin + 1),
                    int(ymax - ymin + 1)
                ]

                rle = mask_util.encode(
                    np.array(mask[:, :, np.newaxis], order='F',
                             dtype='uint8'))[0]
                rle['counts'] = rle['counts'].decode('utf-8')
                anno['segmentation'] = rle
                anno['area'] = int(mask_util.area(rle))
                annotations.append(anno)
            progressbar.update()

        ann_dict['images'] = images
        ann_dict['categories'] = category_dict
        ann_dict['annotations'] = annotations
        save_json(ann_dict, out_file)


def prepare_panoptic_annotations(dataset_dir: str):
    dataset_dir = Path(dataset_dir)

    for name, dirname in [('train', 'training'), ('val', 'validation')]:
        image_dir = dataset_dir / 'images' / dirname
        semantic_dir = dataset_dir / 'annotations' / dirname
        instance_dir = dataset_dir / 'annotations_instance' / dirname

        # folder to store panoptic PNGs
        out_folder = dataset_dir / f'ade20k_panoptic_{name}'
        # json with segmentations information
        out_file = dataset_dir / f'ade20k_panoptic_{name}.json'

        mkdir_or_exist(out_folder)

        # catid mapping
        mapping_file = dataset_dir / 'categoryMapping.txt'
        with open(mapping_file, 'r') as f:
            map_id = {}
            for i, line in enumerate(f.readlines()):
                if i == 0:
                    continue
                ins_id, sem_id, _ = line.strip().split()
                map_id[int(ins_id) - 1] = int(sem_id) - 1

        ADE20K_150_CATEGORIES = []
        # ADE20K_SEM_SEG_CATEGORIES = ADE20KPanopticDataset.METAINFO['classes']
        all_classes = ADE20KPanopticDataset.METAINFO['classes']
        thing_classes = ADE20KPanopticDataset.METAINFO['thing_classes']
        stuff_classes = ADE20KPanopticDataset.METAINFO['stuff_classes']
        palette = ADE20KPanopticDataset.METAINFO['palette']
        for cat_id, cat_name in enumerate(all_classes):
            ADE20K_150_CATEGORIES.append({
                'id':
                cat_id,
                'name':
                cat_name,
                'isthing':
                int(cat_id in map_id.values()),
                'color':
                palette[cat_id]
            })
        categories_dict = {cat['id']: cat for cat in ADE20K_150_CATEGORIES}

        panoptic_json_categories = ADE20K_150_CATEGORIES[:]
        panoptic_json_images = []
        panoptic_json_annotations = []

        mapping = {}
        for i, t in enumerate(thing_classes):
            j = list(all_classes).index(t)
            mapping[j] = i
        for i, t in enumerate(stuff_classes):
            j = list(all_classes).index(t)
            mapping[j] = i + len(thing_classes)

        filenames = sorted(list(image_dir.iterdir()))
        progressbar = ProgressBar(len(filenames))
        for filename in filenames:
            panoptic_json_image = {}

            image_id = filename.stem

            panoptic_json_image['id'] = image_id
            panoptic_json_image['file_name'] = filename.name

            original_format = np.array(Image.open(filename))
            panoptic_json_image['height'] = original_format.shape[0]
            panoptic_json_image['width'] = original_format.shape[1]

            pan_seg = np.zeros(
                (original_format.shape[0], original_format.shape[1], 3),
                dtype=np.uint8)
            id_generator = IdGenerator(categories_dict)

            filename_semantic = semantic_dir / f'{image_id}.png'
            filename_instance = instance_dir / f'{image_id}.png'

            sem_seg = np.array(Image.open(filename_semantic))
            ins_seg = np.array(Image.open(filename_instance))

            assert sem_seg.dtype == np.uint8
            assert ins_seg.dtype == np.uint8

            semantic_cat_ids = sem_seg - 1
            instance_cat_ids = ins_seg[..., 0] - 1
            # instance id starts from 1!
            # because 0 is reserved as VOID label
            instance_ins_ids = ins_seg[..., 1]

            segm_info = []

            # process stuffs
            for semantic_cat_id in np.unique(semantic_cat_ids):
                if semantic_cat_id == 255:
                    continue
                if categories_dict[semantic_cat_id]['isthing'] == 1:
                    continue
                mask = semantic_cat_ids == semantic_cat_id
                # should not have any overlap
                assert pan_seg[mask].sum() == 0

                segment_id, color = id_generator.get_id_and_color(
                    semantic_cat_id)
                pan_seg[mask] = color

                area = np.sum(mask)
                # bbox computation for a segment
                hor = np.sum(mask, axis=0)
                hor_idx = np.nonzero(hor)[0]
                x = hor_idx[0]
                width = hor_idx[-1] - x + 1
                vert = np.sum(mask, axis=1)
                vert_idx = np.nonzero(vert)[0]
                y = vert_idx[0]
                height = vert_idx[-1] - y + 1
                bbox = [int(x), int(y), int(width), int(height)]

                segm_info.append({
                    'id': int(segment_id),
                    'category_id': mapping[int(semantic_cat_id)],
                    'area': int(area),
                    'bbox': bbox,
                    'iscrowd': 0
                })

            # process things
            for thing_id in np.unique(instance_ins_ids):
                if thing_id == 0:
                    continue
                mask = instance_ins_ids == thing_id
                instance_cat_id = np.unique(instance_cat_ids[mask])
                assert len(instance_cat_id) == 1
                id_ = instance_cat_id[0]
                semantic_cat_id = map_id[id_]

                segment_id, color = id_generator.get_id_and_color(
                    semantic_cat_id)
                pan_seg[mask] = color

                area = np.sum(mask)
                # bbox computation for a segment
                hor = np.sum(mask, axis=0)
                hor_idx = np.nonzero(hor)[0]
                x = hor_idx[-1] - x + 1
                width = hor_idx[-1] - x + 1
                vert = np.sum(mask, axis=1)
                vert_idx = np.nonzero(vert)[0]
                y = vert_idx[0]
                height = vert_idx[-1] - y + 1
                bbox = [int(x), int(y), int(width), int(height)]

                segm_info.append({
                    'id': int(segment_id),
                    'category_id': mapping[int(semantic_cat_id)],
                    'area': int(area),
                    'bbox': bbox,
                    'iscrowd': 0
                })

            panoptic_json_annotation = {
                'image_id': image_id,
                'file_name': image_id + '.png',
                'segments_info': segm_info
            }

            Image.fromarray(pan_seg).save(out_folder / f'{image_id}.png')

            panoptic_json_images.append(panoptic_json_image)
            panoptic_json_annotations.append(panoptic_json_annotation)

            progressbar.update()

        panoptic_json = {
            'images': panoptic_json_images,
            'annotations': panoptic_json_annotations,
            'categories': panoptic_json_categories
        }
        save_json(panoptic_json, out_file)


def main():
    args = parse_args()
    assert args.task in ['panoptic', 'instance']
    src = args.src
    if args.task == 'panoptic':
        annotation_train_path = f'{src}/ade20k_panoptic_train'
        annotation_val_path = f'{src}/ade20k_panoptic_val'
        print('Preparing ADE20K panoptic annotations ...')
        print(
            f'Creating panoptic annotations to {annotation_train_path} and {annotation_val_path} ...'  # noqa
        )
        if os.path.exists(annotation_train_path) or os.path.exists(
                annotation_val_path):
            raise RuntimeError('Panoptic annotations already exist.')
        prepare_panoptic_annotations(src)
        print('Done.')
    else:
        annotation_train_path = f'{src}/ade20k_instance_train'
        annotation_val_path = f'{src}/ade20k_instance_val'
        print('Preparing ADE20K instance annotations ...')
        print(
            f'Creating instance annotations to {annotation_train_path} and {annotation_val_path} ...'  # noqa
        )
        if os.path.exists(annotation_train_path) or os.path.exists(
                annotation_val_path):
            raise RuntimeError('Instance annotations already exist.')
        prepare_instance_annotations(src)
        print('Done.')


if __name__ == '__main__':
    main()
