use std::ops::{Add, Sub};

pub trait Scalar {}

pub trait VectorCommon: Sized {
type T: Scalar;
}

pub trait VectorOpsByValue<Rhs = Self, Output = Self>:
VectorCommon + Add<Rhs, Output = Output> + Sub<Rhs, Output = Output>
{
}

pub trait VectorView<'a>:
VectorOpsByValue<Self, Self::Owned> + VectorOpsByValue<Self::Owned, Self::Owned>
{
type Owned;
}

pub trait Vector: VectorOpsByValue<Self> + for<'a> VectorOpsByValue<Self::View<'a>> {
type View<'a>: VectorView<'a, T = Self::T, Owned = Self>
where
Self: 'a;
}

pub trait MatrixCommon {
type V: Vector;
}

fn main() {}