import numpy as np
import torch

from .registry import ANCHOR_GENERATORS


@ANCHOR_GENERATORS.register_module
class AnchorGenerator(object):
    """Standard anchor generator for 2D anchor-based detectors

    Args:
        strides (list[int]): Strides of anchors in multiple feture levels.
        base_size (list[int]): The basic sizes of anchors in multiple levels.
        scales (list[int]): Anchor scales for anchors in a single level.
        ratios (list[float]): The list of ratios between the height and width
            of anchors in a single level.
        scale_major (bool): Whether to multiply scales first when generating
            base anchors. If true, the anchors in the same row will have the
            same scales. By default it is True in V2.0
        centers (list[tuple[float, float]] | None): The centers of the anchor
            relative to the feature grid center in multiple feature levels.
            By default it is set to be None and not used. If a list of tuple of
            float is given, they will be used to shift the centers of anchors.
        center_offset (float): The offset of center in propotion to anchors'
            width and height. By default it is 0 in V2.0.

    Examples:
        >>> from mmdet.core import AnchorGenerator
        >>> self = AnchorGenerator([16], [1.], [1.], [9])
        >>> all_anchors = self.grid_anchors([(2, 2)], device='cpu')
        >>> print(all_anchors)
        [tensor([[-4.5000, -4.5000,  4.5000,  4.5000],
                [11.5000, -4.5000, 20.5000,  4.5000],
                [-4.5000, 11.5000,  4.5000, 20.5000],
                [11.5000, 11.5000, 20.5000, 20.5000]])]
        >>> self = AnchorGenerator([16, 32], [1.], [1.], [9, 18])
        >>> all_anchors = self.grid_anchors([(2, 2), (1, 1)], device='cpu')
        >>> print(all_anchors)
        [tensor([[-4.5000, -4.5000,  4.5000,  4.5000],
                [11.5000, -4.5000, 20.5000,  4.5000],
                [-4.5000, 11.5000,  4.5000, 20.5000],
                [11.5000, 11.5000, 20.5000, 20.5000]]), \
        tensor([[-9., -9., 9., 9.]])]
    """

    def __init__(self,
                 strides,
                 ratios,
                 scales=None,
                 base_sizes=None,
                 scale_major=True,
                 octave_base_scale=None,
                 scales_per_octave=None,
                 centers=None,
                 center_offset=0.):
        if center_offset != 0:
            assert centers is None, 'center cannot be set when center_offset' \
                '=0, {} is given.'.format(centers)
        if not (0 <= center_offset <= 1):
            raise ValueError('center_offset should be in range [0, 1], {} is'
                             ' given.'.format(center_offset))

        # calculate base sizes of anchors
        self.strides = strides
        self.base_sizes = list(strides) if base_sizes is None else base_sizes
        assert len(self.base_sizes) == len(self.strides), \
            'The number of strides should be the same as base sizes, got ' \
            '{} and {}'.format(self.strides, self.base_sizes)

        # calculate scales of anchors
        assert ((octave_base_scale is not None
                and scales_per_octave is not None) ^ (scales is not None)), \
            'scales and octave_base_scale with scales_per_octave cannot' \
            ' be set at the same time'
        if scales is not None:
            self.scales = torch.Tensor(scales)
        elif octave_base_scale is not None and scales_per_octave is not None:
            octave_scales = np.array(
                [2**(i / scales_per_octave) for i in range(scales_per_octave)])
            scales = octave_scales * octave_base_scale
            self.scales = torch.Tensor(scales)
        else:
            raise ValueError('Either scales or octave_base_scale with '
                             'scales_per_octave should be set')

        self.octave_base_scale = octave_base_scale
        self.scales_per_octave = scales_per_octave
        self.ratios = torch.Tensor(ratios)
        self.scale_major = scale_major
        self.centers = centers
        self.center_offset = center_offset
        self.base_anchors = self.gen_base_anchors()

    @property
    def num_base_anchors(self):
        return [base_anchors.size(0) for base_anchors in self.base_anchors]

    def gen_base_anchors(self):
        multi_level_base_anchors = []
        for i, base_size in enumerate(self.base_sizes):
            center = None
            if self.centers is not None:
                center = self.centers[i]
            multi_level_base_anchors.append(
                self.gen_single_level_base_anchors(
                    base_size,
                    scales=self.scales,
                    ratios=self.ratios,
                    center=center))
        return multi_level_base_anchors

    def gen_single_level_base_anchors(self,
                                      base_size,
                                      scales,
                                      ratios,
                                      center=None):
        w = base_size
        h = base_size
        if center is None:
            x_center = self.center_offset * w
            y_center = self.center_offset * h
        else:
            x_center, y_center = center

        h_ratios = torch.sqrt(ratios)
        w_ratios = 1 / h_ratios
        if self.scale_major:
            ws = (w * w_ratios[:, None] * scales[None, :]).view(-1)
            hs = (h * h_ratios[:, None] * scales[None, :]).view(-1)
        else:
            ws = (w * scales[:, None] * w_ratios[None, :]).view(-1)
            hs = (h * scales[:, None] * h_ratios[None, :]).view(-1)

        # use float anchor and the anchor's center is aligned with the
        # pixel center
        base_anchors = [
            x_center - 0.5 * ws, y_center - 0.5 * hs, x_center + 0.5 * ws,
            y_center + 0.5 * hs
        ]
        base_anchors = torch.stack(base_anchors, dim=-1)

        return base_anchors

    def _meshgrid(self, x, y, row_major=True):
        xx = x.repeat(len(y))
        yy = y.view(-1, 1).repeat(1, len(x)).view(-1)
        if row_major:
            return xx, yy
        else:
            return yy, xx

    def grid_anchors(self, featmap_sizes, device='cuda'):
        """Generate grid anchors in multiple feature levels

        Args:
            featmap_sizes (list[tuple]): List of feature map sizes in
                multiple feature levels.
            device (str): Device where the anchors will be put on.

        Return:
            list(torch.Tensor): Anchors in multiple feature levels.
                The sizes of each tensor should be [N, 4], where
                N = width * height, width and height are the sizes of
                the corresponding feature lavel.
        """
        num_levels = len(featmap_sizes)
        assert num_levels == len(self.strides)
        multi_level_anchors = []
        for i in range(num_levels):
            anchors = self.single_level_grid_anchors(
                self.base_anchors[i].to(device),
                featmap_sizes[i],
                self.strides[i],
                device=device)
            multi_level_anchors.append(anchors)
        return multi_level_anchors

    def single_level_grid_anchors(self,
                                  base_anchors,
                                  featmap_size,
                                  stride=16,
                                  device='cuda'):
        feat_h, feat_w = featmap_size
        shift_x = torch.arange(0, feat_w, device=device) * stride
        shift_y = torch.arange(0, feat_h, device=device) * stride
        shift_xx, shift_yy = self._meshgrid(shift_x, shift_y)
        shifts = torch.stack([shift_xx, shift_yy, shift_xx, shift_yy], dim=-1)
        shifts = shifts.type_as(base_anchors)
        # first feat_w elements correspond to the first row of shifts
        # add A anchors (1, A, 4) to K shifts (K, 1, 4) to get
        # shifted anchors (K, A, 4), reshape to (K*A, 4)

        all_anchors = base_anchors[None, :, :] + shifts[:, None, :]
        all_anchors = all_anchors.view(-1, 4)
        # first A rows correspond to A anchors of (0, 0) in feature map,
        # then (0, 1), (0, 2), ...
        return all_anchors

    def valid_flags(self, featmap_sizes, pad_shape, device='cuda'):
        """Generate valid flags of anchors in multiple feature levels

        Args:
            featmap_sizes (list(tuple)): List of feature map sizes in
                multiple feature levels.
            pad_shape (tuple): The padded shape of the image.
            device (str): Device where the anchors will be put on.

        Return:
            list(torch.Tensor): Valid flags of anchors in multiple levels.
        """
        num_levels = len(featmap_sizes)
        multi_level_flags = []
        for i in range(num_levels):
            anchor_stride = self.strides[i]
            feat_h, feat_w = featmap_sizes[i]
            h, w = pad_shape[:2]
            valid_feat_h = min(int(np.ceil(h / anchor_stride)), feat_h)
            valid_feat_w = min(int(np.ceil(w / anchor_stride)), feat_w)
            flags = self.single_level_valid_flags((feat_h, feat_w),
                                                  (valid_feat_h, valid_feat_w),
                                                  self.num_base_anchors[i],
                                                  device=device)
            multi_level_flags.append(flags)
        return multi_level_flags

    def single_level_valid_flags(self,
                                 featmap_size,
                                 valid_size,
                                 num_base_anchors,
                                 device='cuda'):
        feat_h, feat_w = featmap_size
        valid_h, valid_w = valid_size
        assert valid_h <= feat_h and valid_w <= feat_w
        valid_x = torch.zeros(feat_w, dtype=torch.uint8, device=device)
        valid_y = torch.zeros(feat_h, dtype=torch.uint8, device=device)
        valid_x[:valid_w] = 1
        valid_y[:valid_h] = 1
        valid_xx, valid_yy = self._meshgrid(valid_x, valid_y)
        valid = valid_xx & valid_yy
        valid = valid[:, None].expand(valid.size(0),
                                      num_base_anchors).contiguous().view(-1)
        return valid

    def __repr__(self):
        indent_str = '    '
        repr_str = self.__class__.__name__ + '(\n'
        repr_str += '{}strides={},\n'.format(indent_str, self.strides)
        repr_str += '{}ratios={},\n'.format(indent_str, self.ratios)
        repr_str += '{}scales={},\n'.format(indent_str, self.scales)
        repr_str += '{}base_sizes={},\n'.format(indent_str, self.base_sizes)
        repr_str += '{}scale_major={},\n'.format(indent_str, self.scale_major)
        repr_str += '{}octave_base_scale={},\n'.format(indent_str,
                                                       self.octave_base_scale)
        repr_str += '{}scales_per_octave={},\n'.format(indent_str,
                                                       self.scales_per_octave)
        repr_str += '{}centers={},\n'.format(indent_str, self.centers)
        repr_str += '{}center_offset={})'.format(indent_str,
                                                 self.center_offset)
        return repr_str


@ANCHOR_GENERATORS.register_module
class SSDAnchorGenerator(AnchorGenerator):
    """Anchor generator for SSD

    Args:
        strides (list[int]): Strides of anchors in multiple feture levels.
        ratios (list[float]): The list of ratios between the height and width
            of anchors in a single level.
        num_levels (int): Number of feature levels
        basesize_ratio_range (tuple(float)): Ratio range of anchors.
        input_size (int): Size of feature map, 300 for SSD300, 512 for SSD512.
        scale_major (bool): Whether to multiply scales first when generating
            base anchors. If true, the anchors in the same row will have the
            same scales. It is always set to be False in SSD.
    """

    def __init__(self,
                 strides,
                 ratios,
                 num_levels,
                 basesize_ratio_range,
                 input_size=300,
                 scale_major=True):
        self.num_levels = num_levels
        self.strides = strides
        self.input_size = input_size
        self.centers = [(stride / 2., stride / 2.) for stride in strides]
        self.basesize_ratio_range = basesize_ratio_range

        # calculate anchor ratios and sizes
        min_ratio, max_ratio = basesize_ratio_range
        min_ratio = int(min_ratio * 100)
        max_ratio = int(max_ratio * 100)
        step = int(np.floor(max_ratio - min_ratio) / (num_levels - 2))
        min_sizes = []
        max_sizes = []
        for r in range(int(min_ratio), int(max_ratio) + 1, step):
            min_sizes.append(int(input_size * r / 100))
            max_sizes.append(int(input_size * (r + step) / 100))
        if input_size == 300:
            if basesize_ratio_range[0] == 0.15:  # SSD300 COCO
                min_sizes.insert(0, int(input_size * 7 / 100))
                max_sizes.insert(0, int(input_size * 15 / 100))
            elif basesize_ratio_range[0] == 0.2:  # SSD300 VOC
                min_sizes.insert(0, int(input_size * 10 / 100))
                max_sizes.insert(0, int(input_size * 20 / 100))
        elif input_size == 512:
            if basesize_ratio_range[0] == 0.1:  # SSD512 COCO
                min_sizes.insert(0, int(input_size * 4 / 100))
                max_sizes.insert(0, int(input_size * 10 / 100))
            elif basesize_ratio_range[0] == 0.15:  # SSD512 VOC
                min_sizes.insert(0, int(input_size * 7 / 100))
                max_sizes.insert(0, int(input_size * 15 / 100))

        anchor_ratios = []
        anchor_scales = []
        for k in range(len(self.strides)):
            scales = [1., np.sqrt(max_sizes[k] / min_sizes[k])]
            anchor_ratio = [1.]
            for r in ratios[k]:
                anchor_ratio += [1 / r, r]  # 4 or 6 ratio
            anchor_ratios.append(torch.Tensor(anchor_ratio))
            anchor_scales.append(torch.Tensor(scales))

        self.base_sizes = min_sizes
        self.scales = anchor_scales
        self.ratios = anchor_ratios
        self.scale_major = scale_major
        self.center_offset = 0
        self.base_anchors = self.gen_base_anchors()

    def gen_base_anchors(self):
        multi_level_base_anchors = []
        for i, base_size in enumerate(self.base_sizes):
            base_anchors = self.gen_single_level_base_anchors(
                base_size,
                scales=self.scales[i],
                ratios=self.ratios[i],
                center=self.centers[i])
            indices = list(range(len(self.ratios[i])))
            indices.insert(1, len(indices))
            base_anchors = torch.index_select(base_anchors, 0,
                                              torch.LongTensor(indices))
            multi_level_base_anchors.append(base_anchors)
        return multi_level_base_anchors

    def __repr__(self):
        indent_str = '    '
        repr_str = self.__class__.__name__ + '(\n'
        repr_str += '{}strides={},\n'.format(indent_str, self.strides)
        repr_str += '{}scales={},\n'.format(indent_str, self.scales)
        repr_str += '{}num_levels={},\n'.format(indent_str, self.num_levels)
        repr_str += '{}scale_major={},\n'.format(indent_str, self.scale_major)
        repr_str += '{}input_size={},\n'.format(indent_str, self.input_size)
        repr_str += '{}scales={},\n'.format(indent_str, self.scales)
        repr_str += '{}ratios={},\n'.format(indent_str, self.ratios)
        repr_str += '{}base_sizes={},\n'.format(indent_str, self.base_sizes)
        repr_str += '{}basesize_ratio_range={})'.format(
            indent_str, self.basesize_ratio_range)
        return repr_str


@ANCHOR_GENERATORS.register_module
class LegacyAnchorGenerator(AnchorGenerator):
    """Legacy anchor generator used in MMDetection V1.x

    Difference to the V2.0 anchor generator:
    1. The center offset of V1.x anchors are set to be 0.5 rather than 0.
    2. The width/height are minused by 1 when calculating the anchors' centers
       and corners to meet the V1.x coordinate system.
    3. The anchors' corners are quantized.

    Args:
        strides (list[int]): Strides of anchors in multiple feture levels.
        base_size (list[int]): The basic sizes of anchors in multiple levels.
        scales (list[int]): Anchor scales for anchors in a single level.
        ratios (list[float]): The list of ratios between the height and width
            of anchors in a single level.
        scale_major (bool): Whether to multiply scales first when generating
            base anchors. If true, the anchors in the same row will have the
            same scales. By default it is True in V2.0
        centers (list[tuple[float, float]] | None): The centers of the anchor
            relative to the feature grid center in multiple feature levels.
            By default it is set to be None and not used. It a list of float
            is given, this list will be used to shift the centers of anchors.
        center_offset (float): The offset of center in propotion to anchors'
            width and height. By default it is 0 in V2.0.

    Examples:
        >>> from mmdet.core import LegacyAnchorGenerator
        >>> self = LegacyAnchorGenerator(
        >>>     [16], [1.], [1.], [9], center_offset=0.5)
        >>> all_anchors = self.grid_anchors(((2, 2),), device='cpu')
        >>> print(all_anchors)
        [tensor([[ 0.,  0.,  8.,  8.],
                [16.,  0., 24.,  8.],
                [ 0., 16.,  8., 24.],
                [16., 16., 24., 24.]])]
    """

    def gen_single_level_base_anchors(self,
                                      base_size,
                                      scales,
                                      ratios,
                                      center=None):
        w = base_size
        h = base_size
        if center is None:
            x_center = self.center_offset * (w - 1)
            y_center = self.center_offset * (h - 1)
        else:
            x_center, y_center = self.center

        h_ratios = torch.sqrt(self.ratios)
        w_ratios = 1 / h_ratios
        if self.scale_major:
            ws = (w * w_ratios[:, None] * scales[None, :]).view(-1)
            hs = (h * h_ratios[:, None] * scales[None, :]).view(-1)
        else:
            ws = (w * scales[:, None] * w_ratios[None, :]).view(-1)
            hs = (h * scales[:, None] * h_ratios[None, :]).view(-1)

        # use float anchor and the anchor's center is aligned with the
        # pixel center
        base_anchors = [
            x_center - 0.5 * (ws - 1), y_center - 0.5 * (hs - 1),
            x_center + 0.5 * (ws - 1), y_center + 0.5 * (hs - 1)
        ]
        base_anchors = torch.stack(base_anchors, dim=-1).round()

        return base_anchors


@ANCHOR_GENERATORS.register_module
class LegacySSDAnchorGenerator(LegacyAnchorGenerator, SSDAnchorGenerator):
    """Legacy anchor generator used in MMDetection V1.x

    The difference between `LegacySSDAnchorGenerator` and `SSDAnchorGenerator`
    can be found in `LegacyAnchorGenerator`.
    """
