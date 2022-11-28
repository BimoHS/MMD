# Copyright (c) OpenMMLab. All rights reserved.
from typing import Dict, Tuple

from mmengine.model import uniform_init
from torch import Tensor, nn

from mmdet.registry import MODELS
from ..layers import SinePositionalEncoding
from ..layers.transformer import (DabDetrTransformerDecoder,
                                  DabDetrTransformerEncoder, inverse_sigmoid)
from .detr import DETR


@MODELS.register_module()
class DABDETR(DETR):
    r"""Implementation of `DAB-DETR:
    Dynamic Anchor Boxes are Better Queries for DETR.

    <https://arxiv.org/abs/2201.12329>`_.

    Code is modified from the `official github repo
    <https://github.com/IDEA-Research/DAB-DETR>`_.

    Args:
        random_refpoints_xy (bool): Whether to randomly initialize query
            embeddings and not update them during training.
            Defaults to False.
        num_patterns (int): Inspired by Anchor-DETR. Defaults to 0.
    """

    def __init__(self,
                 *args,
                 random_refpoints_xy: bool = False,
                 num_patterns: int = 0,
                 **kwargs) -> None:
        self.random_refpoints_xy = random_refpoints_xy
        assert isinstance(num_patterns, int), \
            f'num_patterns should be int but {num_patterns}.'
        self.num_patterns = num_patterns

        super().__init__(*args, **kwargs)

    def _init_layers(self) -> None:
        """Initialize layers except for backbone, neck and bbox_head."""
        self.positional_encoding = SinePositionalEncoding(
            **self.positional_encoding_cfg)
        self.encoder = DabDetrTransformerEncoder(**self.encoder)
        self.decoder = DabDetrTransformerDecoder(**self.decoder)
        self.embed_dims = self.encoder.embed_dims
        self.query_dim = self.decoder.query_dim
        self.query_embedding = nn.Embedding(self.num_queries, self.query_dim)
        if self.num_patterns > 0:
            self.patterns = nn.Embedding(self.num_patterns, self.embed_dims)
        self.nb_dec = self.decoder.num_layers

        num_feats = self.positional_encoding.num_feats
        assert num_feats * 2 == self.embed_dims, \
            f'embed_dims should be exactly 2 times of num_feats. ' \
            f'Found {self.embed_dims} and {num_feats}.'

    def init_weights(self) -> None:
        """Initialize weights for Transformer and other components."""
        super(DABDETR, self).init_weights()
        if self.random_refpoints_xy:
            uniform_init(self.query_embedding)
            self.query_embedding.weight.data[:, :2] = \
                inverse_sigmoid(self.query_embedding.weight.data[:, :2])
            self.query_embedding.weight.data[:, :2].requires_grad = False

    def pre_decoder(self, memory: Tensor) -> Tuple[Dict, Dict]:
        """Prepare intermediate variables before entering Transformer decoder,
        such as `query`, `query_pos`.

        Args:
            memory (Tensor): The output embeddings of the Transformer encoder,
                has shape (num_feat_points, bs, dim).

        Returns:
            tuple[dict, dict]: The first dict contains the inputs of decoder
            and the second dict contains the inputs of the bbox_head function.

            - decoder_inputs_dict (dict): The keyword args dictionary of
                `self.forward_decoder()`, which includes 'query', 'query_pos',
                'memory' and 'reg_branches'.
            - head_inputs_dict (dict): The keyword args dictionary of the
                bbox_head functions, which is usually empty, or includes
                `enc_outputs_class` and `enc_outputs_class` when the detector
                support 'two stage' or 'query selection' strategies.
        """
        batch_size = memory.size(1)
        query_pos = self.query_embedding.weight
        query_pos = query_pos.unsqueeze(1).repeat(1, batch_size, 1)
        if self.num_patterns == 0:
            query = query_pos.new_zeros(self.num_queries, batch_size,
                                        self.embed_dims)
        else:
            query = self.patterns.weight[:, None, None, :]\
                .repeat(1, self.num_queries, batch_size, 1)\
                .view(-1, batch_size, self.embed_dims)
            query_pos = query_pos.repeat(self.num_patterns, 1, 1)
        # iterative refinement for anchor boxes
        reg_branches = self.bbox_head.fc_reg

        decoder_inputs_dict = dict(
            query_pos=query_pos,
            query=query,
            memory=memory,
            reg_branches=reg_branches)
        head_inputs_dict = dict()
        return decoder_inputs_dict, head_inputs_dict

    def forward_decoder(self, query: Tensor, query_pos: Tensor, memory: Tensor,
                        memory_mask: Tensor, memory_pos: Tensor,
                        reg_branches: nn.Module) -> Dict:
        """Forward with Transformer decoder.

        Args:
            query (Tensor): The queries of decoder inputs, has shape
                (num_queries, bs, dim).
            query_pos (Tensor): The positional queries of decoder inputs,
                has shape (num_queries, bs, dim).
            memory (Tensor): The output embeddings of the Transformer encoder,
                has shape (num_feat, bs, dim).
            memory_mask (Tensor): ByteTensor, the padding mask of the memory,
                has shape (bs, num_feat).
            memory_pos (Tensor): The positional embeddings of memory, has
                shape (num_feat, bs, dim).
            reg_branches (nn.Module): The regression branch for dynamically
                updating references in each layer.

        Returns:
            dict: The dictionary of decoder outputs, which includes the
            `hidden_states` and `references` of the decoder output.
        """
        hidden_states, references = self.decoder(
            query=query,
            key=memory,
            query_pos=query_pos,
            key_pos=memory_pos,
            key_padding_mask=memory_mask,
            reg_branches=reg_branches)
        head_inputs_dict = dict(
            hidden_states=hidden_states, references=references)
        return head_inputs_dict
