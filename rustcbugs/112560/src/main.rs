#![feature(associated_const_equality)]

trait Trait2 {}

trait Trait1 {
    const A: u32;
    type A;
}

impl<T: Trait1<A = 12>, const N: usize> Trait2 for [T; N] {}

fn main() {}