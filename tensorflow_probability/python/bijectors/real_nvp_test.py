# Copyright 2018 The TensorFlow Probability Authors.
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
# ============================================================================
"""Tests for MaskedAutoregressiveFlow."""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

# Dependency imports
import numpy as np
import tensorflow.compat.v1 as tf1
import tensorflow.compat.v2 as tf

from tensorflow_probability.python import bijectors as tfb
from tensorflow_probability.python import distributions as tfd
from tensorflow_probability.python.internal import tensorshape_util
from tensorflow_probability.python.internal import test_util as tfp_test_util
from tensorflow.python.framework import test_util  # pylint: disable=g-direct-tensorflow-import


@test_util.run_all_in_graph_and_eager_modes
class RealNVPTest(tfp_test_util.TestCase):

  def testBijectorWithTrivialTransform(self):
    flat_x_ = np.random.normal(0., 1., 8).astype(np.float32)
    batched_x_ = np.random.normal(0., 1., (3, 8)).astype(np.float32)
    for x_ in [flat_x_, batched_x_]:
      nvp = tfb.RealNVP(
          num_masked=4,
          validate_args=True,
          shift_and_log_scale_fn=lambda x, _: (x, x),
          is_constant_jacobian=False)
      x = tf.constant(x_)
      forward_x = nvp.forward(x)
      # Use identity to invalidate cache.
      inverse_y = nvp.inverse(tf.identity(forward_x))
      forward_inverse_y = nvp.forward(inverse_y)
      fldj = nvp.forward_log_det_jacobian(x, event_ndims=1)
      # Use identity to invalidate cache.
      ildj = nvp.inverse_log_det_jacobian(tf.identity(forward_x), event_ndims=1)
      forward_x_ = self.evaluate(forward_x)
      inverse_y_ = self.evaluate(inverse_y)
      forward_inverse_y_ = self.evaluate(forward_inverse_y)
      ildj_ = self.evaluate(ildj)
      fldj_ = self.evaluate(fldj)

      self.assertStartsWith(nvp.name, 'real_nvp')
      self.assertAllClose(forward_x_, forward_inverse_y_, rtol=1e-4, atol=0.)
      self.assertAllClose(x_, inverse_y_, rtol=1e-4, atol=0.)
      self.assertAllClose(ildj_, -fldj_, rtol=1e-6, atol=0.)

  def testBijectorWithReverseMask(self):
    flat_x_ = np.random.normal(0., 1., 8).astype(np.float32)
    batched_x_ = np.random.normal(0., 1., (3, 8)).astype(np.float32)
    num_masked = -5
    for x_ in [flat_x_, batched_x_]:
      flip_nvp = tfb.RealNVP(
          num_masked=num_masked,
          validate_args=True,
          shift_and_log_scale_fn=tfb.real_nvp_default_template(
              hidden_layers=[3], shift_only=False),
          is_constant_jacobian=False)

      _, x2_ = np.split(x_, [8 - abs(num_masked)], axis=-1)
      x = tf.constant(x_)

      # Check latter half is the same after passing thru reversed mask RealNVP.
      forward_x = flip_nvp.forward(x)
      _, forward_x2 = tf.split(forward_x, [8 - abs(num_masked),
                                           abs(num_masked)], axis=-1)
      self.evaluate(tf1.global_variables_initializer())
      forward_x2_ = self.evaluate(forward_x2)

      self.assertAllClose(forward_x2_, x2_, rtol=1e-4, atol=0.)

  def testBijectorConditionKwargs(self):
    batch_size = 3
    x_ = np.linspace(-1.0, 1.0, (batch_size * 4 * 2)).astype(
        np.float32).reshape((batch_size, 4 * 2))

    conditions = {
        'a': tf.random.normal((batch_size, 4), dtype=tf.float32, seed=584),
        'b': tf.random.normal((batch_size, 2), dtype=tf.float32, seed=9817),
    }

    def _condition_shift_and_log_scale_fn(x0, output_units, a, b):
      x = tf.concat((x0, a, b), axis=-1)
      out = tf1.layers.dense(inputs=x, units=2 * output_units)
      shift, log_scale = tf.split(out, 2, axis=-1)
      return shift, log_scale

    condition_shift_and_log_scale_fn = tf1.make_template(
        'real_nvp_condition_template', _condition_shift_and_log_scale_fn)

    nvp = tfb.RealNVP(
        num_masked=4,
        validate_args=True,
        is_constant_jacobian=False,
        shift_and_log_scale_fn=condition_shift_and_log_scale_fn)

    x = tf.constant(x_)

    forward_x = nvp.forward(x, **conditions)
    # Use identity to invalidate cache.
    inverse_y = nvp.inverse(tf.identity(forward_x), **conditions)
    forward_inverse_y = nvp.forward(inverse_y, **conditions)
    fldj = nvp.forward_log_det_jacobian(x, event_ndims=1, **conditions)
    # Use identity to invalidate cache.
    ildj = nvp.inverse_log_det_jacobian(
        tf.identity(forward_x), event_ndims=1, **conditions)
    self.evaluate(tf1.global_variables_initializer())
    [
        forward_x_,
        inverse_y_,
        forward_inverse_y_,
        ildj_,
        fldj_,
    ] = self.evaluate([
        forward_x,
        inverse_y,
        forward_inverse_y,
        ildj,
        fldj,
    ])
    self.assertStartsWith(nvp.name, 'real_nvp')
    self.assertAllClose(forward_x_, forward_inverse_y_, rtol=1e-5, atol=1e-5)
    self.assertAllClose(x_, inverse_y_, rtol=1e-5, atol=1e-5)
    self.assertAllClose(ildj_, -fldj_, rtol=1e-5, atol=1e-5)

  def testMatrixBijectorRaises(self):
    with self.assertRaisesRegexp(
        ValueError,
        'Bijectors with `forward_min_event_ndims` > 1 are not supported'):

      def bijector_fn(*args, **kwargs):
        del args, kwargs
        return tfb.Inline(forward_min_event_ndims=2)
      rnvp = tfb.RealNVP(1, bijector_fn=bijector_fn, validate_args=True)
      rnvp.forward([1., 2.])

  def testRankChangingBijectorRaises(self):
    with self.assertRaisesRegexp(
        ValueError, 'Bijectors which alter `event_ndims` are not supported.'):

      def bijector_fn(*args, **kwargs):
        del args, kwargs
        return tfb.Inline(forward_min_event_ndims=1, inverse_min_event_ndims=0)
      rnvp = tfb.RealNVP(1, bijector_fn=bijector_fn, validate_args=True)
      rnvp.forward([1., 2.])


@test_util.run_all_in_graph_and_eager_modes
class RealNVPTestKwargs(
    tfp_test_util.VectorDistributionTestHelpers,
    tfp_test_util.TestCase):

  @property
  def _real_nvp_kwargs(self):
    return {
        'shift_and_log_scale_fn':
            tfb.real_nvp_default_template(hidden_layers=[3], shift_only=False),
        'is_constant_jacobian':
            False,
    }

  def testBatchedBijectorWithMLPTransform(self):
    x_ = np.random.normal(0., 1., (3, 8)).astype(np.float32)
    nvp = tfb.RealNVP(
        num_masked=4, validate_args=True, **self._real_nvp_kwargs)
    x = tf.constant(x_)
    forward_x = nvp.forward(x)
    # Use identity to invalidate cache.
    inverse_y = nvp.inverse(tf.identity(forward_x))
    forward_inverse_y = nvp.forward(inverse_y)
    fldj = nvp.forward_log_det_jacobian(x, event_ndims=1)
    # Use identity to invalidate cache.
    ildj = nvp.inverse_log_det_jacobian(tf.identity(forward_x), event_ndims=1)
    self.evaluate(tf1.global_variables_initializer())
    [
        forward_x_,
        inverse_y_,
        forward_inverse_y_,
        ildj_,
        fldj_,
    ] = self.evaluate([
        forward_x,
        inverse_y,
        forward_inverse_y,
        ildj,
        fldj,
    ])
    self.assertStartsWith(nvp.name, 'real_nvp')
    self.assertAllClose(forward_x_, forward_inverse_y_, rtol=1e-4, atol=0.)
    self.assertAllClose(x_, inverse_y_, rtol=1e-4, atol=0.)
    self.assertAllClose(ildj_, -fldj_, rtol=1e-6, atol=0.)

  def testNonBatchedBijectorWithMLPTransform(self):
    x_ = np.random.normal(0., 1., (8,)).astype(np.float32)
    nvp = tfb.RealNVP(
        num_masked=4, validate_args=True, **self._real_nvp_kwargs)
    x = tf.constant(x_)
    forward_x = nvp.forward(x)
    # Use identity to invalidate cache.
    inverse_y = nvp.inverse(tf.identity(forward_x))
    forward_inverse_y = nvp.forward(inverse_y)
    fldj = nvp.forward_log_det_jacobian(x, event_ndims=1)
    # Use identity to invalidate cache.
    ildj = nvp.inverse_log_det_jacobian(tf.identity(forward_x), event_ndims=1)
    self.evaluate(tf1.global_variables_initializer())
    [
        forward_x_,
        inverse_y_,
        forward_inverse_y_,
        ildj_,
        fldj_,
    ] = self.evaluate([
        forward_x,
        inverse_y,
        forward_inverse_y,
        ildj,
        fldj,
    ])
    self.assertStartsWith(nvp.name, 'real_nvp')
    self.assertAllClose(forward_x_, forward_inverse_y_, rtol=1e-4, atol=0.)
    self.assertAllClose(x_, inverse_y_, rtol=1e-4, atol=0.)
    self.assertAllClose(ildj_, -fldj_, rtol=1e-6, atol=0.)

  def testMutuallyConsistent(self):
    dims = 4
    nvp = tfb.RealNVP(
        num_masked=3, validate_args=True, **self._real_nvp_kwargs)
    dist = tfd.TransformedDistribution(
        distribution=tfd.Normal(loc=0., scale=1.),
        bijector=nvp,
        event_shape=[dims],
        validate_args=True)
    self.run_test_sample_consistent_log_prob(
        sess_run_fn=self.evaluate,
        dist=dist,
        num_samples=int(1e6),
        seed=54819,
        radius=1.,
        center=0.,
        rtol=0.1)

  def testInvertMutuallyConsistent(self):
    dims = 4
    nvp = tfb.Invert(
        tfb.RealNVP(
            num_masked=3, validate_args=True, **self._real_nvp_kwargs))
    dist = tfd.TransformedDistribution(
        distribution=tfd.Normal(loc=0., scale=1.),
        bijector=nvp,
        event_shape=[dims],
        validate_args=True)
    self.run_test_sample_consistent_log_prob(
        sess_run_fn=self.evaluate,
        dist=dist,
        num_samples=int(1e6),
        seed=22197,
        radius=1.,
        center=0.,
        rtol=0.1)


@test_util.run_all_in_graph_and_eager_modes
class NICETest(RealNVPTestKwargs):

  @property
  def _real_nvp_kwargs(self):
    return {
        'shift_and_log_scale_fn':
            tfb.real_nvp_default_template(hidden_layers=[2], shift_only=True),
        'is_constant_jacobian':
            True,
    }


@test_util.run_all_in_graph_and_eager_modes
class RealNVPConstantShiftScaleTest(RealNVPTestKwargs):

  @property
  def _real_nvp_kwargs(self):

    def constant_shift_log_scale_fn(x0, output_units):
      del x0, output_units
      shift = tf.constant([0.1])
      log_scale = tf.constant([0.5])
      return shift, log_scale

    return {
        'shift_and_log_scale_fn': constant_shift_log_scale_fn,
        'is_constant_jacobian': True,
    }


def _make_gated_bijector_fn():
  def _bijector_fn(x, output_units):
    if tensorshape_util.rank(x.shape) == 1:
      x = x[tf.newaxis, ...]
      reshape_output = lambda x: x[0]
    else:
      reshape_output = lambda x: x

    out = tf1.layers.dense(inputs=x, units=2 * output_units)
    shift, logit_gate = tf.split(out, 2, axis=-1)
    shift = reshape_output(shift)
    logit_gate = reshape_output(logit_gate)
    gate = tf.nn.sigmoid(logit_gate)
    return tfb.AffineScalar(shift=(1. - gate) * shift, scale=gate)
  return tf1.make_template('gated_bijector', _bijector_fn)


@test_util.run_all_in_graph_and_eager_modes
class GatedTest(RealNVPTestKwargs):

  @property
  def _real_nvp_kwargs(self):
    return {
        'bijector_fn': _make_gated_bijector_fn(),
    }

if __name__ == '__main__':
  tf.test.main()
