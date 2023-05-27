# Copyright (c) OpenMMLab. All rights reserved.
import torch


def images_to_levels(target, num_levels):
    """将target由图像级别转换为特征级别.

    [target_img0, target_img1] -> [target_level0, target_level1, ...]
    以上为抽象表示,下面为具体表示. 注意h和w随着level变化而变化
    [[nl * h * w * na,], ] * bs -> [[bs, h * w * na], ] * nl
    """
    # [bs, *target.shape] 注意下面的:操作,即获取所有bs维度相应层级上的数据
    target = torch.stack(target, 0)
    level_targets = []
    start = 0
    for n in num_levels:
        end = start + n
        # level_targets.append(target[:, start:end].squeeze(0))
        level_targets.append(target[:, start:end])
        start = end
    return level_targets


def anchor_inside_flags(flat_anchors,
                        valid_flags,
                        img_shape,
                        allowed_border=0):
    """检查anchor是否在图像边界内.

    Args:
        flat_anchors (torch.Tensor): Flatten anchors, shape (n, 4).
        valid_flags (torch.Tensor): An existing valid flags of anchors.
        img_shape (tuple(int)): Shape of current image.
        allowed_border (int, optional): 允许超出anchor边界的大小.负值表示不限制

    Returns:
        torch.Tensor: Flags indicating whether the anchors are inside a \
            valid range.
    """
    img_h, img_w = img_shape[:2]
    if allowed_border >= 0:
        inside_flags = valid_flags & \
            (flat_anchors[:, 0] >= -allowed_border) & \
            (flat_anchors[:, 1] >= -allowed_border) & \
            (flat_anchors[:, 2] < img_w + allowed_border) & \
            (flat_anchors[:, 3] < img_h + allowed_border)
    else:
        inside_flags = valid_flags
    return inside_flags


def calc_region(bbox, ratio, featmap_size=None):
    """Calculate a proportional bbox region.

    The bbox center are fixed and the new h' and w' is h * ratio and w * ratio.

    Args:
        bbox (Tensor): Bboxes to calculate regions, shape (n, 4).
        ratio (float): Ratio of the output region.
        featmap_size (tuple): Feature map size used for clipping the boundary.

    Returns:
        tuple: x1, y1, x2, y2
    """
    x1 = torch.round((1 - ratio) * bbox[0] + ratio * bbox[2]).long()
    y1 = torch.round((1 - ratio) * bbox[1] + ratio * bbox[3]).long()
    x2 = torch.round(ratio * bbox[0] + (1 - ratio) * bbox[2]).long()
    y2 = torch.round(ratio * bbox[1] + (1 - ratio) * bbox[3]).long()
    if featmap_size is not None:
        x1 = x1.clamp(min=0, max=featmap_size[1])
        y1 = y1.clamp(min=0, max=featmap_size[0])
        x2 = x2.clamp(min=0, max=featmap_size[1])
        y2 = y2.clamp(min=0, max=featmap_size[0])
    return (x1, y1, x2, y2)
