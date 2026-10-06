use crate_b::{Foo, Super, Unsatisfied};

fn foo<T: Foo>() {
   unsatisfied::<<T::FooAssoc as Super>::SuperAssoc>()
}

fn unsatisfied<B: Unsatisfied>() {}

fn main() {}