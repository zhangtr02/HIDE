#![forbid(coherence_leak_check)]
#![feature(negative_impls, with_negative_coherence)]

pub trait Marker {}

impl<'a, T: ?Sized> !Marker for &'a T {}

trait FnMarker {}

impl<T: ?Sized + Marker> FnMarker for fn(T) {}
impl<T: ?Sized> FnMarker for fn(&T) {}

fn main() {}