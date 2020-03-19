# Copyright 2020 LMNT, Inc. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================

import haste_pytorch_lib as LIB
import torch
import torch.nn as nn
import torch.nn.functional as F


__all__ = [
    'IndRNN'
]


#@torch.jit.script
def IndRNNScript(
    training: bool,
    input,
    h0,
    kernel,
    recurrent_scale,
    bias):
  time_steps = input.shape[0]

  h = [h0]
  Wx = input @ kernel + bias
  for t in range(time_steps):
    h.append(torch.tanh(Wx[t] + h[-1] * recurrent_scale))
  h = torch.stack(h)
  return h


class IndRNNFunction(torch.autograd.Function):
  @staticmethod
  def forward(ctx, training, *inputs):
    h = LIB.indrnn_forward(training, *inputs)
    ctx.save_for_backward(inputs[0], *inputs[2:], h)
    ctx.training = training
    return h

  @staticmethod
  def backward(ctx, grad_h):
    if not ctx.training:
      raise RuntimeError('IndRNN backward can only be called in training mode')
    saved = [*ctx.saved_tensors]
    saved[0] = saved[0].permute(2, 0, 1).contiguous()
    saved[1] = saved[1].permute(1, 0).contiguous()
    grads = LIB.indrnn_backward(*saved, grad_h.contiguous())
    return (None, *grads)


class IndRNN(nn.Module):
  def __init__(
      self,
      input_size,
      hidden_size,
      batch_first=False):
    super(IndRNN, self).__init__()

    self.input_size = input_size
    self.hidden_size = hidden_size
    self.batch_first = batch_first

    gpu = torch.device('cuda')
    self.kernel = nn.Parameter(torch.empty(input_size, hidden_size, device=gpu))
    self.recurrent_scale = nn.Parameter(torch.empty(hidden_size, device=gpu))
    self.bias = nn.Parameter(torch.empty(hidden_size, device=gpu))
    self.reset_parameters()

  def reset_parameters(self):
    nn.init.xavier_uniform_(self.kernel)
    nn.init.uniform_(self.recurrent_scale, -1.0, 1.0)
    nn.init.zeros_(self.bias)

  def forward(self, input, state=None, lengths=None):
    if self.batch_first:
      input = input.permute(1, 0, 2)

    if state is None:
      h0 = torch.zeros(input.shape[1], self.hidden_size, dtype=input.dtype, device=input.device)
    elif state.shape[0] != 1:
      raise ValueError('initial state for IndRNN must have leading dimesion of 1')
    else:
      h0 = state[0]

    h = self._impl(input, h0)

    if lengths is not None:
      cols = range(h.size(1))
      state = h[[lengths, cols]].unsqueeze(0)
    else:
      state = h[-1].unsqueeze(0)

    output = h[1:]
    if self.batch_first:
      output = output.permute(1, 0, 2)

    return output, state

  def _impl(self, input, state):
    tensors = [input, self.kernel, self.recurrent_scale, self.bias]
    is_cuda = [tensor.is_cuda for tensor in tensors]
    if any(is_cuda) and not all(is_cuda):
      raise ValueError('IndRNN tensors should all be CUDA tensors or none should be CUDA tensors')

    if all(is_cuda):
      return IndRNNFunction.apply(
        self.training,
        input.contiguous(),
        state.contiguous(),
        self.kernel.contiguous(),
        self.recurrent_scale.contiguous(),
        self.bias.contiguous())
    else:
      return IndRNNScript(
        self.training,
        input.contiguous(),
        state.contiguous(),
        self.kernel.contiguous(),
        self.recurrent_scale.contiguous(),
        self.bias.contiguous())
