# Copyright 2022 The JaxGaussianProcesses Contributors. All Rights Reserved.
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

import abc
import functools as ft
import beartype.typing as tp

from flax.experimental import nnx
import jax.numpy as jnp
from jaxtyping import Float, Num
import tensorflow_probability.substrates.jax.distributions as tfd

from gpjax.kernels.computations import (
    AbstractKernelComputation,
    DenseKernelComputation,
)
from gpjax.typing import (
    Array,
    ScalarFloat,
)


@nnx.dataclass
class AbstractKernel(nnx.Module):
    r"""Base kernel class."""

    n_dims: int = nnx.field(init=False)
    active_dims: tp.Union[list[int], int, slice] = 1
    compute_engine: AbstractKernelComputation = nnx.field(
        default=DenseKernelComputation(), repr=False
    )
    name: str = "AbstractKernel"

    def __post_init__(self):
        self.n_dims, self.active_dims = _check_active_dims(self.active_dims)

    def cross_covariance(self, x: Num[Array, "N D"], y: Num[Array, "M D"]):
        return self.compute_engine.cross_covariance(self, x, y)

    def gram(self, x: Num[Array, "N D"]):
        return self.compute_engine.gram(self, x)

    def slice_input(self, x: Float[Array, "... D"]) -> Float[Array, "... Q"]:
        r"""Slice out the relevant columns of the input matrix.

        Select the relevant columns of the supplied matrix to be used within the
        kernel's evaluation.

        Args:
            x (Float[Array, "... D"]): The matrix or vector that is to be sliced.

        Returns
        -------
            Float[Array, "... Q"]: A sliced form of the input matrix.
        """
        return x[..., self.active_dims] if self.active_dims is not None else x

    @abc.abstractmethod
    def __call__(
        self,
        x: Num[Array, " D"],
        y: Num[Array, " D"],
    ) -> ScalarFloat:
        r"""Evaluate the kernel on a pair of inputs.

        Args:
            x (Num[Array, " D"]): The left hand input of the kernel function.
            y (Num[Array, " D"]): The right hand input of the kernel function.

        Returns
        -------
            ScalarFloat: The evaluated kernel function at the supplied inputs.
        """
        raise NotImplementedError

    def __add__(
        self, other: tp.Union["AbstractKernel", ScalarFloat]
    ) -> "AbstractKernel":
        r"""Add two kernels together.
        Args:
            other (AbstractKernel): The kernel to be added to the current kernel.

        Returns
        -------
            AbstractKernel: A new kernel that is the sum of the two kernels.
        """
        if isinstance(other, AbstractKernel):
            return SumKernel(kernels=[self, other])
        else:
            return SumKernel(kernels=[self, Constant(other)])

    def __radd__(
        self, other: tp.Union["AbstractKernel", ScalarFloat]
    ) -> "AbstractKernel":
        r"""Add two kernels together.
        Args:
            other (AbstractKernel): The kernel to be added to the current kernel.

        Returns
        -------
            AbstractKernel: A new kernel that is the sum of the two kernels.
        """
        return self.__add__(other)

    def __mul__(
        self, other: tp.Union["AbstractKernel", ScalarFloat]
    ) -> "AbstractKernel":
        r"""Multiply two kernels together.

        Args:
            other (AbstractKernel): The kernel to be multiplied with the current kernel.

        Returns
        -------
            AbstractKernel: A new kernel that is the product of the two kernels.
        """
        if isinstance(other, AbstractKernel):
            return ProductKernel(kernels=[self, other])
        else:
            return ProductKernel(kernels=[self, Constant(other)])

    @property
    def spectral_density(self) -> tp.Union[tfd.Distribution, None]:
        return None


@nnx.dataclass
class Constant(AbstractKernel):
    r"""
    A constant kernel. This kernel evaluates to a constant for all inputs.
    The scalar value itself can be treated as a model hyperparameter and learned during training.
    """

    constant: ScalarFloat = nnx.variable_field(nnx.Param, default=jnp.array(0.0))

    def __call__(self, x: Float[Array, " D"], y: Float[Array, " D"]) -> ScalarFloat:
        r"""Evaluate the kernel on a pair of inputs.

        Args:
            x (Float[Array, " D"]): The left hand input of the kernel function.
            y (Float[Array, " D"]): The right hand input of the kernel function.

        Returns
        -------
            ScalarFloat: The evaluated kernel function at the supplied inputs.
        """
        return self.constant.squeeze()


@nnx.dataclass
class CombinationKernel(AbstractKernel):
    r"""A base class for products or sums of MeanFunctions."""

    kernels: list[AbstractKernel] = None
    operator: tp.Callable = None

    def __post_init__(self):
        # Add kernels to a list, flattening out instances of this class therein, as in GPFlow kernels.
        kernels_list: list[AbstractKernel] = []

        for kernel in self.kernels:
            if not isinstance(kernel, AbstractKernel):
                raise TypeError("can only combine Kernel instances")  # pragma: no cover

            if isinstance(kernel, self.__class__):
                kernels_list.extend(kernel.kernels)
            else:
                kernels_list.append(kernel)

        self.kernels = kernels_list

    def __call__(
        self,
        x: Float[Array, " D"],
        y: Float[Array, " D"],
    ) -> ScalarFloat:
        r"""Evaluate the kernel on a pair of inputs.

        Args:
            x (Float[Array, " D"]): The left hand input of the kernel function.
            y (Float[Array, " D"]): The right hand input of the kernel function.

        Returns
        -------
            ScalarFloat: The evaluated kernel function at the supplied inputs.
        """
        return self.operator(jnp.stack([k(x, y) for k in self.kernels]))


@tp.overload
def _check_active_dims(active_dims: list[int]) -> list[int]:
    ...


@tp.overload
def _check_active_dims(active_dims: int) -> slice:
    ...


def _check_active_dims(active_dims: list[int] | int | slice):
    if isinstance(a := active_dims, list):
        return len(a), active_dims
    elif isinstance(a, int):
        return a, slice(None)
    elif isinstance(a, slice):
        step = a.step if a.step is not None else 1
        return (a.stop - a.start) // step, a
    else:
        raise ValueError("active_dims must be a list, int or slice.")


SumKernel = ft.partial(CombinationKernel, operator=jnp.sum)
ProductKernel = ft.partial(CombinationKernel, operator=jnp.prod)
