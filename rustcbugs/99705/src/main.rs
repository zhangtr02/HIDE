// build-pass
#![crate_type = "lib"]
#![feature(generic_const_exprs)]
#![allow(incomplete_features)]
trait MyIterator {
    type Output;
}

trait Foo {
    const ABC: usize;
}

struct IteratorStruct<const N: usize>{

}

struct BarStruct<const N: usize> {
    data: [usize; N]
}

impl<const N: usize> MyIterator for IteratorStruct<N> {
    type Output = BarStruct<N>;
}

fn test<T: Foo>() -> impl MyIterator<Output = BarStruct<{T::ABC}>> where [(); {T::ABC}]: Sized {
    IteratorStruct::<{T::ABC}>{}
}

fn main() {}