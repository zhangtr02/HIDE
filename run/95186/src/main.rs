#![feature(rustc_attrs)]
#![feature(min_specialization)]
#![feature(const_trait_impl)]

#[rustc_specialization_trait]
trait Specialize {}

trait Foo {}

impl<T> const Foo for T {}

impl<T> const Foo for T
where
    T: ~const Specialize
{}

fn main() {}