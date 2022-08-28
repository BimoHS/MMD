# Copyright (c) OpenMMLab. All rights reserved.
import copy
import json
import os.path as osp
import tempfile
from collections import OrderedDict
from multiprocessing import Process, Queue
from typing import Dict, List, Optional, Sequence, Union

import numpy as np
from mmengine.evaluator import BaseMetric
from mmengine.logging import MMLogger
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import maximum_bipartite_matching
from tqdm import tqdm

from mmdet.registry import METRICS

PERSON_CLASSES = ['background', 'person']


@METRICS.register_module()
class CrowdHumanMetric(BaseMetric):

    def __init__(self,
                 ann_file: str,
                 metric: Union[str, List[str]] = 'AP',
                 format_only: bool = False,
                 outfile_prefix: Optional[str] = None,
                 collect_device: str = 'cpu',
                 prefix: Optional[str] = None) -> None:
        super().__init__(collect_device=collect_device, prefix=prefix)

        self.ann_file = ann_file
        # crowdhuman evaluation metrics
        self.metrics = metric if isinstance(metric, list) else [metric]
        allowed_metrics = ['MR', 'AP', 'JI']
        for metric in self.metrics:
            if metric not in allowed_metrics:
                raise KeyError(f"metric should be one of 'MR', 'AP', 'JI',"
                               f'but got {metric}.')

        self.format_only = format_only
        if self.format_only:
            assert outfile_prefix is not None, 'outfile_prefix must be not'
            'None when format_only is True, otherwise the result files will'
            'be saved to a temp directory which will be cleaned up at the end.'
        self.outfile_prefix = outfile_prefix

    @staticmethod
    def results2json(results: Sequence[dict], outfile_prefix: str) -> str:
        """Dump the detection results to a json file."""
        result_file_path = f'{outfile_prefix}.json'
        with open(result_file_path, 'w') as outfile:
            for i, result in enumerate(results):
                ann, pred = result
                dump_dict = dict()
                dump_dict['ID'] = ann['ID']
                dump_dict['width'] = ann['width']
                dump_dict['height'] = ann['height']
                dtboxes = []
                bboxes = pred.tolist()
                for _, single_bbox in enumerate(bboxes):
                    temp_dict = dict()
                    x1, y1, x2, y2, score = single_bbox
                    temp_dict['box'] = [x1, y1, x2 - x1, y2 - y1]
                    temp_dict['score'] = score
                    temp_dict['tag'] = 1
                    dtboxes.append(temp_dict)
                dump_dict['dtboxes'] = dtboxes
                outfile.write(json.dumps(dump_dict) + '\n')

        return result_file_path

    def process(self, data_batch: Sequence[dict],
                predictions: Sequence[dict]) -> None:
        """Process one batch of data samples and predictions. The processed
        results should be stored in ``self.results``, which will be used to
        compute the metrics when all batches have been processed.

        Args:
            data_batch (Sequence[dict]): A batch of data
                from the dataloader.
            predictions (Sequence[dict]): A batch of outputs from
                the model.
        """
        for data, pred in zip(data_batch, predictions):
            gt = copy.deepcopy(data['data_sample'])
            ann = dict()

            ann['ID'] = gt['img_id']
            ann['width'] = gt['ori_shape'][1]
            ann['height'] = gt['ori_shape'][0]

            pred_bboxes = pred['pred_instances']['bboxes'].cpu().numpy()
            pred_scores = pred['pred_instances']['scores'].cpu().numpy()

            pred_bbox_scores = np.hstack(
                [pred_bboxes, pred_scores.reshape((-1, 1))])

            self.results.append((ann, pred_bbox_scores))

    def compute_metrics(self, results: list) -> Dict[str, float]:
        """Compute the metrics from processed results.

        Args:
            results (list): The processed results of each batch.

        Returns:
            Dict[str, float]: The computed metrics. The keys are the names of
            the metrics, and the values are corresponding results.
        """
        logger: MMLogger = MMLogger.get_current_instance()

        tmp_dir = None
        if self.outfile_prefix is None:
            tmp_dir = tempfile.TemporaryDirectory()
            outfile_prefix = osp.join(tmp_dir.name, 'result')
        else:
            outfile_prefix = self.outfile_prefix

        # convert predictions to coco format and dump to json file
        result_files = self.results2json(results, outfile_prefix)
        eval_results = OrderedDict()
        if self.format_only:
            logger.info(f'results are saved in {osp.dirname(outfile_prefix)}')
            return eval_results

        database = Database(self.ann_file, result_files, 'box', None, 0)
        database.compare()
        # return dict(bbox_mAP=AP, mMR=mMR, recall=recall)
        for metric in self.metrics:
            logger.info(f'Evaluating {metric}...')
            if metric == 'AP':
                AP, recall, _ = database.eval_AP()
                eval_results['mAP'] = float(f'{round(AP, 4)}')
                eval_results['recall'] = float(f'{round(recall, 4)}')
            if metric == 'MR':
                mMR, _ = database.eval_MR()
                eval_results['mMR'] = float(f'{round(mMR, 4)}')
            if metric == 'JI':
                _, JI = database.eval_JI()
                eval_results['JI'] = float(f'{round(JI, 4)}')
        if tmp_dir is not None:
            tmp_dir.cleanup()

        return eval_results


class Image(object):
    """Data structure for evaluation of CrowdHuman.

    Args:
        mode (int): Select the mode of evaluate. Valid mode include
            0(just body box), 1(just head box) and 2(both of them).
            Defaults to 0.
    """

    def __init__(self, mode):
        self.ID = None
        self._width = None
        self._height = None
        self.dtboxes = None
        self.gtboxes = None
        self.eval_mode = mode

        self._ignNum = None
        self._gtNum = None
        self._dtNum = None

    def load(self, record, body_key, head_key, class_names, gt_flag):
        """Loading information for evaluation.

        Args:
            record (dict): Label information or test results.
                The format might look something like this:
                {
                    'ID': '273271,c9db000d5146c15',
                    'gtboxes': [
                        {'fbox': [72, 202, 163, 503], 'tag': 'person', ...},
                        {'fbox': [199, 180, 144, 499], 'tag': 'person', ...},
                        ...
                    ]
                }
                or:
                {
                    'ID': '273271,c9db000d5146c15',
                    'width': 800,
                    'height': 1067,
                    'dtboxes': [
                        {
                            'box': [306.22, 205.95, 164.05, 394.04],
                            'score': 0.99,
                            'tag': 1
                        },
                        {
                            'box': [403.60, 178.66, 157.15, 421.33],
                            'score': 0.99,
                            'tag': 1
                        },
                        ...
                    ]
                }
            body_key (str): key of detection body box.
                Valid when loading detection results and self.eval_mode!=1.
            head_key (str): key of detection head box.
                Valid when loading detection results and self.eval_mode!=0.
            class_names (list[str]):class names of data set.
                Defaults to ['background', 'person'].
            gt_flag (bool): Indicate whether record is ground truth
                or predicting the outcome.
        """
        if 'ID' in record and self.ID is None:
            self.ID = record['ID']
        if 'width' in record and self._width is None:
            self._width = record['width']
        if 'height' in record and self._height is None:
            self._height = record['height']
        if gt_flag:
            self._gtNum = len(record['gtboxes'])
            body_bbox, head_bbox = self.load_gt_boxes(record, 'gtboxes',
                                                      class_names)
            if self.eval_mode == 0:
                self.gtboxes = body_bbox
                self._ignNum = (body_bbox[:, -1] == -1).sum()
            elif self.eval_mode == 1:
                self.gtboxes = head_bbox
                self._ignNum = (head_bbox[:, -1] == -1).sum()
            elif self.eval_mode == 2:
                gt_tag = np.array([
                    body_bbox[i, -1] != -1 and head_bbox[i, -1] != -1
                    for i in range(len(body_bbox))
                ])
                self._ignNum = (gt_tag == 0).sum()
                self.gtboxes = np.hstack((body_bbox[:, :-1], head_bbox[:, :-1],
                                          gt_tag.reshape(-1, 1)))
            else:
                raise Exception('Unknown evaluation mode!')
        if not gt_flag:
            self._dtNum = len(record['dtboxes'])
            if self.eval_mode == 0:
                self.dtboxes = self.load_det_boxes(record, 'dtboxes', body_key,
                                                   'score')
            elif self.eval_mode == 1:
                self.dtboxes = self.load_det_boxes(record, 'dtboxes', head_key,
                                                   'score')
            elif self.eval_mode == 2:
                body_dtboxes = self.load_det_boxes(record, 'dtboxes', body_key)
                head_dtboxes = self.load_det_boxes(record, 'dtboxes', head_key,
                                                   'score')
                self.dtboxes = np.hstack((body_dtboxes, head_dtboxes))
            else:
                raise Exception('Unknown evaluation mode!')

    def compare_caltech(self, thres):
        """Match the detection results with the ground_truth by Caltech
        matching strategy.

        Args:
            thres (float): IOU threshold.

        Returns:
            scorelist: a list of tuples (dtbox, imageID),
                in the descending sort of dtbox.score.
        """
        if self.dtboxes is None or self.gtboxes is None:
            return list()

        dtboxes = self.dtboxes if self.dtboxes is not None else list()
        gtboxes = self.gtboxes if self.gtboxes is not None else list()
        dt_matched = np.zeros(dtboxes.shape[0])
        gt_matched = np.zeros(gtboxes.shape[0])

        dtboxes = np.array(sorted(dtboxes, key=lambda x: x[-1], reverse=True))
        gtboxes = np.array(sorted(gtboxes, key=lambda x: x[-1], reverse=True))
        if len(dtboxes):
            overlap_iou = self.box_overlap_opr(dtboxes, gtboxes, True)
            overlap_ioa = self.box_overlap_opr(dtboxes, gtboxes, False)
        else:
            return list()

        scorelist = list()
        for i, dt in enumerate(dtboxes):
            maxpos = -1
            maxiou = thres
            for j, gt in enumerate(gtboxes):
                if gt_matched[j] == 1:
                    continue
                if gt[-1] > 0:
                    overlap = overlap_iou[i][j]
                    if overlap > maxiou:
                        maxiou = overlap
                        maxpos = j
                else:
                    if maxpos >= 0:
                        break
                    else:
                        overlap = overlap_ioa[i][j]
                        if overlap > thres:
                            maxiou = overlap
                            maxpos = j
            if maxpos >= 0:
                if gtboxes[maxpos, -1] > 0:
                    gt_matched[maxpos] = 1
                    dt_matched[i] = 1
                    scorelist.append((dt, 1, self.ID))
                else:
                    dt_matched[i] = -1
            else:
                dt_matched[i] = 0
                scorelist.append((dt, 0, self.ID))
        return scorelist

    def compare_caltech_union(self, thres):
        """
        :meth: match the detection results with the groundtruth by Caltech
        matching strategy
        :param thres: iou threshold
        :type thres: float
        :return: a list of tuples (dtbox, imageID), in the descending sort
        of dtbox.score
        """
        dtboxes = self.dtboxes if self.dtboxes is not None else list()
        gtboxes = self.gtboxes if self.gtboxes is not None else list()
        if len(dtboxes) == 0:
            return list()
        dt_matched = np.zeros(dtboxes.shape[0])
        gt_matched = np.zeros(gtboxes.shape[0])

        dtboxes = np.array(sorted(dtboxes, key=lambda x: x[-1], reverse=True))
        gtboxes = np.array(sorted(gtboxes, key=lambda x: x[-1], reverse=True))
        dt_body_boxes = np.hstack((dtboxes[:, :4], dtboxes[:, -1][:, None]))
        dt_head_boxes = dtboxes[:, 4:8]
        gt_body_boxes = np.hstack((gtboxes[:, :4], gtboxes[:, -1][:, None]))
        gt_head_boxes = gtboxes[:, 4:8]
        overlap_iou = self.box_overlap_opr(dt_body_boxes, gt_body_boxes, True)
        overlap_head = self.box_overlap_opr(dt_head_boxes, gt_head_boxes, True)
        overlap_ioa = self.box_overlap_opr(dt_body_boxes, gt_body_boxes, False)

        scorelist = list()
        for i, dt in enumerate(dtboxes):
            maxpos = -1
            maxiou = thres
            for j, gt in enumerate(gtboxes):
                if gt_matched[j] == 1:
                    continue
                if gt[-1] > 0:
                    o_body = overlap_iou[i][j]
                    o_head = overlap_head[i][j]
                    if o_body > maxiou and o_head > maxiou:
                        maxiou = o_body
                        maxpos = j
                else:
                    if maxpos >= 0:
                        break
                    else:
                        o_body = overlap_ioa[i][j]
                        if o_body > thres:
                            maxiou = o_body
                            maxpos = j
            if maxpos >= 0:
                if gtboxes[maxpos, -1] > 0:
                    gt_matched[maxpos] = 1
                    dt_matched[i] = 1
                    scorelist.append((dt, 1, self.ID))
                else:
                    dt_matched[i] = -1
            else:
                dt_matched[i] = 0
                scorelist.append((dt, 0, self.ID))
        return scorelist

    @staticmethod
    def box_overlap_opr(dboxes: np.ndarray, gboxes: np.ndarray, if_iou):
        eps = 1e-6
        assert dboxes.shape[-1] >= 4 and gboxes.shape[-1] >= 4
        N, K = dboxes.shape[0], gboxes.shape[0]
        dtboxes = np.tile(np.expand_dims(dboxes, axis=1), (1, K, 1))
        gtboxes = np.tile(np.expand_dims(gboxes, axis=0), (N, 1, 1))

        iw = (
            np.minimum(dtboxes[:, :, 2], gtboxes[:, :, 2]) -
            np.maximum(dtboxes[:, :, 0], gtboxes[:, :, 0]))
        ih = (
            np.minimum(dtboxes[:, :, 3], gtboxes[:, :, 3]) -
            np.maximum(dtboxes[:, :, 1], gtboxes[:, :, 1]))
        inter = np.maximum(0, iw) * np.maximum(0, ih)

        dtarea = (dtboxes[:, :, 2] - dtboxes[:, :, 0]) * (
            dtboxes[:, :, 3] - dtboxes[:, :, 1])
        if if_iou:
            gtarea = (gtboxes[:, :, 2] - gtboxes[:, :, 0]) * (
                gtboxes[:, :, 3] - gtboxes[:, :, 1])
            ious = inter / (dtarea + gtarea - inter + eps)
        else:
            ious = inter / (dtarea + eps)
        return ious

    def clip_all_boader(self):
        """Make sure boxes are within the image range."""

        def _clip_boundary(boxes, height, width):
            assert boxes.shape[-1] >= 4
            boxes[:, 0] = np.minimum(np.maximum(boxes[:, 0], 0), width - 1)
            boxes[:, 1] = np.minimum(np.maximum(boxes[:, 1], 0), height - 1)
            boxes[:, 2] = np.maximum(np.minimum(boxes[:, 2], width), 0)
            boxes[:, 3] = np.maximum(np.minimum(boxes[:, 3], height), 0)
            return boxes

        assert self.dtboxes.shape[-1] >= 4
        assert self.gtboxes.shape[-1] >= 4
        assert self._width is not None and self._height is not None
        if self.eval_mode == 2:
            self.dtboxes[:, :4] = _clip_boundary(self.dtboxes[:, :4],
                                                 self._height, self._width)
            self.gtboxes[:, :4] = _clip_boundary(self.gtboxes[:, :4],
                                                 self._height, self._width)
            self.dtboxes[:, 4:8] = _clip_boundary(self.dtboxes[:, 4:8],
                                                  self._height, self._width)
            self.gtboxes[:, 4:8] = _clip_boundary(self.gtboxes[:, 4:8],
                                                  self._height, self._width)
        else:
            self.dtboxes = _clip_boundary(self.dtboxes, self._height,
                                          self._width)
            self.gtboxes = _clip_boundary(self.gtboxes, self._height,
                                          self._width)

    @staticmethod
    def load_gt_boxes(dict_input, key_name, class_names):
        """load ground_truth and transform [x, y, w, h] to [x1, y1, x2, y2]"""
        assert key_name in dict_input
        if len(dict_input[key_name]) < 1:
            return np.empty([0, 5])
        head_bbox = []
        body_bbox = []
        for rb in dict_input[key_name]:
            if rb['tag'] in class_names:
                body_tag = class_names.index(rb['tag'])
                head_tag = copy.deepcopy(body_tag)
            else:
                body_tag = -1
                head_tag = -1
            if 'extra' in rb:
                if 'ignore' in rb['extra']:
                    if rb['extra']['ignore'] != 0:
                        body_tag = -1
                        head_tag = -1
            if 'head_attr' in rb:
                if 'ignore' in rb['head_attr']:
                    if rb['head_attr']['ignore'] != 0:
                        head_tag = -1
            head_bbox.append(np.hstack((rb['hbox'], head_tag)))
            body_bbox.append(np.hstack((rb['fbox'], body_tag)))
        head_bbox = np.array(head_bbox)
        head_bbox[:, 2:4] += head_bbox[:, :2]
        body_bbox = np.array(body_bbox)
        body_bbox[:, 2:4] += body_bbox[:, :2]
        return body_bbox, head_bbox

    @staticmethod
    def load_det_boxes(dict_input, key_name, key_box, key_score, key_tag=None):
        assert key_name in dict_input
        if len(dict_input[key_name]) < 1:
            return np.empty([0, 5])
        else:
            assert key_box in dict_input[key_name][0]
            if key_score:
                assert key_score in dict_input[key_name][0]
            if key_tag:
                assert key_tag in dict_input[key_name][0]
        if key_score:
            if key_tag:
                bboxes = np.vstack([
                    np.hstack((rb[key_box], rb[key_score], rb[key_tag]))
                    for rb in dict_input[key_name]
                ])
            else:
                bboxes = np.vstack([
                    np.hstack((rb[key_box], rb[key_score]))
                    for rb in dict_input[key_name]
                ])
        else:
            if key_tag:
                bboxes = np.vstack([
                    np.hstack((rb[key_box], rb[key_tag]))
                    for rb in dict_input[key_name]
                ])
            else:
                bboxes = np.vstack(
                    [rb[key_box] for rb in dict_input[key_name]])
        bboxes[:, 2:4] += bboxes[:, :2]
        return bboxes

    def compare_voc(self, thres):
        """
        :meth: match the detection results with the groundtruth by VOC
        matching strategy
        :param thres: iou threshold
        :type thres: float
        :return: a list of tuples (dtbox, imageID), in the descending
        sort of dtbox.score
        """
        if self.dtboxes is None:
            return list()
        dtboxes = self.dtboxes
        gtboxes = self.gtboxes if self.gtboxes is not None else list()
        dtboxes.sort(key=lambda x: x.score, reverse=True)
        gtboxes.sort(key=lambda x: x.ign)

        scorelist = list()
        for i, dt in enumerate(dtboxes):
            maxpos = -1
            maxiou = thres

            for j, gt in enumerate(gtboxes):
                overlap = dt.iou(gt)
                if overlap > maxiou:
                    maxiou = overlap
                    maxpos = j

            if maxpos >= 0:
                if gtboxes[maxpos].ign == 0:
                    gtboxes[maxpos].matched = 1
                    dtboxes[i].matched = 1
                    scorelist.append((dt, self.ID))
                else:
                    dtboxes[i].matched = -1
            else:
                dtboxes[i].matched = 0
                scorelist.append((dt, self.ID))
        return scorelist


class Database(object):
    """Loading information and evaluation.

    Args:
        gt_path (str): Path of an annotations file.
        dt_path (str): Path of detection results.
        body_key (str, None): key of detection body box.
            Valid when loading detection results and mode!=1.
        head_key (str, None): key of detection head box.
            Valid when loading detection results and mode!=0.
        mode (int): Select the mode of evaluate. Valid mode include
            0(just body box), 1(just head box) and 2(both of them).
            Defaults to 0.
    Examples:
        >>> database = Database(
        >>>     '/data/YuYoujiang/dataset/CrowdHuman/annotation_val.odgt',
        >>>     '/data/YuYoujiang/CrowdDet/mmdetection/tools/result_meg.json',
        >>>     'box', None, 0)
        >>> database.compare()
        >>> eval_results = OrderedDict()
        >>> AP, recall, _ = database.eval_AP()
        >>> eval_results['mAP'] = float(f'{round(AP, 3)}')
        >>> eval_results['recall'] = float(f'{round(recall, 3)}')
        >>> mMR, _ = database.eval_MR()
        >>> eval_results['mMR'] = float(f'{round(mMR, 3)}')
        >>> print(eval_results)
    """

    def __init__(self,
                 gt_path=None,
                 dt_path=None,
                 body_key=None,
                 head_key=None,
                 mode=0):

        self.images = dict()
        self.eval_mode = mode
        self.loadData(gt_path, body_key, head_key, if_gt=True)
        self.loadData(dt_path, body_key, head_key, if_gt=False)

        self._ignNum = sum([self.images[i]._ignNum for i in self.images])
        self._gtNum = sum([self.images[i]._gtNum for i in self.images])
        self._imageNum = len(self.images)
        self.scorelist = None

    def loadData(self, fpath, body_key=None, head_key=None, if_gt=True):
        """Load data from annotations file or detection results. Notice the
        order in which this method is called： Need to load annotation file
        before loading test results.

        Args:
            fpath (str): Path of an annotations file or detection results.
            body_key (str): key of detection full box. Defaults to None.
            head_key (str): key of detection head box. Defaults to None.
            if_gt (bool): Select the mode of evaluate. Valid mode include
                0(just body box), 1(just head box) and 2(both of them).
                Defaults to True.
        """
        assert osp.isfile(fpath), fpath + ' does not exist!'
        with open(fpath, 'r') as f:
            lines = f.readlines()
        records = [json.loads(line.strip('\n')) for line in lines]
        if if_gt:
            for record in records:
                self.images[record['ID']] = Image(self.eval_mode)
                self.images[record['ID']].load(record, body_key, head_key,
                                               PERSON_CLASSES, True)
        else:
            for record in records:
                self.images[record['ID']].load(record, body_key, head_key,
                                               PERSON_CLASSES, False)
                self.images[record['ID']].clip_all_boader()

    def compare(self, thres=0.5, matching=None):
        """Match the detection results with the ground_truth.

        Args:
            thres (float): The processed results of each batch.
            matching(str): Matching rules. Valid rules include
                VOC and None(caltech). Defaults to None.
        Returns:
            list[tuple[ndarray, int, str]]: Matching result.
                The detection bboxes and scores.
                The label of bbox.
                Image ID.
        """
        assert matching is None or matching == 'VOC', matching
        scorelist = list()
        for ID in self.images:
            if matching == 'VOC':
                result = self.images[ID].compare_voc(thres)
            else:
                result = self.images[ID].compare_caltech(thres)
            scorelist.extend(result)
        # In the descending sort of dtbox score.
        scorelist.sort(key=lambda x: x[0][-1], reverse=True)
        self.scorelist = scorelist

    def eval_MR(self, ref='CALTECH_-2'):
        """Evaluate by Caltech-style log-average miss rate.

        Args:
            ref (str): Different ways of calculating. Valid ref include
            CALTECH_-2 and CALTECH_-4. Defaults to CALTECH_-2.

        Returns:
            float: miss rate results.
        """

        # find greater_than
        def _find_gt(lst, target):
            for idx, item in enumerate(lst):
                if item >= target:
                    return idx
            return len(lst) - 1

        assert ref == 'CALTECH_-2' or ref == 'CALTECH_-4', ref
        if ref == 'CALTECH_-2':
            # CALTECH_MRREF_2: anchor points (from 10^-2 to 1) as in
            # P.Dollar's paper
            ref = [
                0.0100, 0.0178, 0.03160, 0.0562, 0.1000, 0.1778, 0.3162,
                0.5623, 1.000
            ]
        else:
            # CALTECH_MRREF_4: anchor points (from 10^-4 to 1) as in
            # S.Zhang's paper
            ref = [
                0.0001, 0.0003, 0.00100, 0.0032, 0.0100, 0.0316, 0.1000,
                0.3162, 1.000
            ]

        if self.scorelist is None:
            self.compare()

        tp, fp = 0.0, 0.0
        fppiX, fppiY = list(), list()
        for i, item in enumerate(self.scorelist):
            if item[1] == 1:
                tp += 1.0
            elif item[1] == 0:
                fp += 1.0

            fn = (self._gtNum - self._ignNum) - tp
            recall = tp / (tp + fn)
            missrate = 1.0 - recall
            fppi = fp / self._imageNum
            fppiX.append(fppi)
            fppiY.append(missrate)

        score = list()
        for pos in ref:
            argmin = _find_gt(fppiX, pos)
            if argmin >= 0:
                score.append(fppiY[argmin])
        score = np.array(score)
        MR = np.exp(np.log(score).mean())
        return MR, (fppiX, fppiY)

    def eval_AP(self):
        """Evaluate by average precision."""

        # calculate general ap score
        def _calculate_map(recall, precision):
            assert len(recall) == len(precision)
            area = 0
            for i in range(1, len(recall)):
                delta_h = (precision[i - 1] + precision[i]) / 2
                delta_w = recall[i] - recall[i - 1]
                area += delta_w * delta_h
            return area

        tp, fp = 0.0, 0.0
        rpX, rpY = list(), list()
        total_gt = self._gtNum - self._ignNum
        total_images = self._imageNum

        fpn = []
        recalln = []
        thr = []
        fppi = []
        for i, item in enumerate(self.scorelist):
            if item[1] == 1:
                tp += 1.0
            elif item[1] == 0:
                fp += 1.0
            fn = total_gt - tp
            recall = tp / (tp + fn)
            precision = tp / (tp + fp)
            rpX.append(recall)
            rpY.append(precision)
            fpn.append(fp)
            recalln.append(tp)
            thr.append(item[0][-1])
            fppi.append(fp / total_images)

        AP = _calculate_map(rpX, rpY)
        return AP, recall, (rpX, rpY, thr, fpn, recalln, fppi)

    def eval_JI(self, nr_procs=10):
        """Evaluate by JI using multi_process.

        Args:
            nr_procs (int): The number of processes.
            Defaults to 10.

        Returns:
            float: jaccard index results.
        """
        import math
        res_line = []
        res_JI = []
        for i in range(10):
            score_thr = 1e-1 * i
            total = len(self.images)
            stride = math.ceil(total / nr_procs)
            result_queue = Queue(10000)
            results, procs = [], []
            records = list(self.images.items())
            for i in range(nr_procs):
                start = i * stride
                end = np.min([start + stride, total])
                sample_data = dict(records[start:end])
                p = Process(
                    target=compute_JI_with_ignore,
                    args=(result_queue, sample_data, score_thr))
                p.start()
                procs.append(p)
            tqdm.monitor_interval = 0
            pbar = tqdm(total=total, leave=False, ascii=True)
            for i in range(total):
                t = result_queue.get()
                results.append(t)
                pbar.update(1)
            for p in procs:
                p.join()
            pbar.close()
            line, mean_ratio = gather(results)
            line = 'score_thr:{:.1f}, {}'.format(score_thr, line)
            res_line.append(line)
            res_JI.append(mean_ratio)
        return res_line, max(res_JI)


def gather(results):
    assert len(results)
    img_num = 0
    for result in results:
        if result['n'] != 0 or result['m'] != 0:
            img_num += 1
    mean_ratio = np.sum([rb['ratio'] for rb in results]) / img_num
    valids = np.sum([rb['k'] for rb in results])
    total = np.sum([rb['n'] for rb in results])
    gtn = np.sum([rb['m'] for rb in results])
    line = 'mean_ratio:{:.4f}, valids:{}, total:{}, gtn:{}' \
        .format(mean_ratio, valids, total, gtn)
    return line, mean_ratio


def compute_JI_with_ignore(result_queue, images, score_thr, bm_thresh=0.5):
    for ID, record in images.items():
        gt_boxes = record.gtboxes
        dt_boxes = record.dtboxes
        keep = dt_boxes[:, -1] > score_thr
        dt_boxes = dt_boxes[keep][:, :-1]

        gt_tag = np.array(gt_boxes[:, -1] != -1)
        matches = compute_matching(dt_boxes, gt_boxes[gt_tag, :4], bm_thresh)
        # get the unmatched_indices
        matched_indices = np.array([j for (j, _) in matches])
        unmatched_indices = list(
            set(np.arange(dt_boxes.shape[0])) - set(matched_indices))
        num_ignore_dt = get_ignores(dt_boxes[unmatched_indices],
                                    gt_boxes[~gt_tag, :4], bm_thresh)
        matched_indices = np.array([j for (_, j) in matches])
        unmatched_indices = list(
            set(np.arange(gt_boxes[gt_tag].shape[0])) - set(matched_indices))
        num_ignore_gt = get_ignores(gt_boxes[gt_tag][unmatched_indices],
                                    gt_boxes[~gt_tag, :4], bm_thresh)
        # compute results
        eps = 1e-6
        k = len(matches)
        m = gt_tag.sum() - num_ignore_gt
        n = dt_boxes.shape[0] - num_ignore_dt
        ratio = k / (m + n - k + eps)
        recall = k / (m + eps)
        cover = k / (n + eps)
        noise = 1 - cover
        result_dict = dict(
            ratio=ratio,
            recall=recall,
            cover=cover,
            noise=noise,
            k=k,
            m=m,
            n=n)
        result_queue.put_nowait(result_dict)


def compute_matching(dt_boxes, gt_boxes, bm_thr):
    assert dt_boxes.shape[-1] > 3 and gt_boxes.shape[-1] > 3
    if dt_boxes.shape[0] < 1 or gt_boxes.shape[0] < 1:
        return list()

    ious = compute_iou_matrix(dt_boxes, gt_boxes)
    input_ = copy.deepcopy(ious)
    input_[input_ < bm_thr] = 0
    match_scipy = maximum_bipartite_matching(
        csr_matrix(input_), perm_type='column')
    matches_ = []
    for i in range(len(match_scipy)):
        if match_scipy[i] != -1:
            matches_.append((i, match_scipy[i]))
    return matches_


def compute_iou_matrix(dboxes: np.ndarray, gboxes: np.ndarray):
    eps = 1e-6
    assert dboxes.shape[-1] >= 4 and gboxes.shape[-1] >= 4
    N, K = dboxes.shape[0], gboxes.shape[0]
    dtboxes = np.tile(np.expand_dims(dboxes, axis=1), (1, K, 1))
    gtboxes = np.tile(np.expand_dims(gboxes, axis=0), (N, 1, 1))

    iw = np.minimum(dtboxes[:, :, 2], gtboxes[:, :, 2]) - np.maximum(
        dtboxes[:, :, 0], gtboxes[:, :, 0])
    ih = np.minimum(dtboxes[:, :, 3], gtboxes[:, :, 3]) - np.maximum(
        dtboxes[:, :, 1], gtboxes[:, :, 1])
    inter = np.maximum(0, iw) * np.maximum(0, ih)

    dtarea = (dtboxes[:, :, 2] - dtboxes[:, :, 0]) * (
        dtboxes[:, :, 3] - dtboxes[:, :, 1])
    gtarea = (gtboxes[:, :, 2] - gtboxes[:, :, 0]) * (
        gtboxes[:, :, 3] - gtboxes[:, :, 1])
    ious = inter / (dtarea + gtarea - inter + eps)
    return ious


def get_ignores(dt_boxes, gt_boxes, bm_thr):
    if gt_boxes.size:
        ioas = compute_ioa_matrix(dt_boxes, gt_boxes)
        ioas = np.max(ioas, axis=1)
        rows = np.where(ioas > bm_thr)[0]
        return len(rows)
    else:
        return 0


def compute_ioa_matrix(dboxes: np.ndarray, gboxes: np.ndarray):
    eps = 1e-6
    assert dboxes.shape[-1] >= 4 and gboxes.shape[-1] >= 4
    N, K = dboxes.shape[0], gboxes.shape[0]
    dtboxes = np.tile(np.expand_dims(dboxes, axis=1), (1, K, 1))
    gtboxes = np.tile(np.expand_dims(gboxes, axis=0), (N, 1, 1))

    iw = np.minimum(dtboxes[:, :, 2], gtboxes[:, :, 2]) - np.maximum(
        dtboxes[:, :, 0], gtboxes[:, :, 0])
    ih = np.minimum(dtboxes[:, :, 3], gtboxes[:, :, 3]) - np.maximum(
        dtboxes[:, :, 1], gtboxes[:, :, 1])
    inter = np.maximum(0, iw) * np.maximum(0, ih)

    dtarea = np.maximum(dtboxes[:, :, 2] - dtboxes[:, :, 0], 0) * np.maximum(
        dtboxes[:, :, 3] - dtboxes[:, :, 1], 0)
    ioas = inter / (dtarea + eps)
    return ioas


if __name__ == '__main__':
    database = Database(
        '/data/YuYoujiang/dataset/CrowdHuman/annotation_val.odgt',
        '/data/YuYoujiang/CrowdDet/mmdetection/tools/result_meg_scale.json',
        'box', None, 0)
    database.compare()
    eval_results = OrderedDict()
    AP, recall, _ = database.eval_AP()
    eval_results['mAP'] = float(f'{round(AP, 4)}')
    eval_results['recall'] = float(f'{round(recall, 4)}')
    mMR, _ = database.eval_MR()
    eval_results['mMR'] = float(f'{round(mMR, 4)}')
    _, JI = database.eval_JI()
    eval_results['JI'] = float(f'{round(JI, 4)}')
    print(eval_results)
