from torch import nn as nn

from mmdet.ops import build_conv_layer, build_norm_layer


class ResLayer(nn.Sequential):

    def __init__(self,
                 block,
                 inplanes,
                 planes,
                 num_blocks,
                 stride=1,
                 avg_down=False,
                 conv_cfg=None,
                 norm_cfg=dict(type='BN'),
                 **kwargs):
        """ResLayer to build ResNet style backbone.

        Args:
            block (nn.Module): block used to build ResLayer
            inplanes (int): inplanes of block
            planes (int): planes of block
            num_blocks (int): number of blocks
            stride (int): stride of the first block
            avg_down (bool): Use AvgPool instead of stride conv when
                downsampling in the bottleneck.
            conv_cfg (dict): dictionary to construct and config conv layer
            norm_cfg (dict): dictionary to construct and config norm layer.
        """
        self.block = block

        downsample = None
        if stride != 1 or inplanes != planes * block.expansion:
            downsample = []
            conv_stride = stride
            if avg_down and stride != 1:
                conv_stride = 1
                downsample.append(
                    nn.AvgPool2d(
                        kernel_size=stride,
                        stride=stride,
                        ceil_mode=True,
                        count_include_pad=False))
            downsample.extend([
                build_conv_layer(
                    conv_cfg,
                    inplanes,
                    planes * block.expansion,
                    kernel_size=1,
                    stride=conv_stride,
                    bias=False),
                build_norm_layer(norm_cfg, planes * block.expansion)[1]
            ])
            downsample = nn.Sequential(*downsample)

        gen_attention = kwargs.pop('gen_attention', None)
        gen_attention_blocks = kwargs.pop('gen_attention_blocks', tuple())
        layers = []
        layers.append(
            block(
                inplanes=inplanes,
                planes=planes,
                stride=stride,
                downsample=downsample,
                gen_attention=gen_attention
                if 0 in gen_attention_blocks else None,
                **kwargs))
        inplanes = planes * block.expansion
        for i in range(1, num_blocks):
            layers.append(
                block(
                    inplanes=inplanes,
                    planes=planes,
                    stride=1,
                    gen_attention=gen_attention if
                    (i in gen_attention_blocks) else None,
                    **kwargs))
        super(ResLayer, self).__init__(*layers)
