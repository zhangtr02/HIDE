#![allow(async_fn_in_trait)]

pub trait Bar {
    type Foo;
}

pub trait Baz {
    async fn boom<X: Bar>() -> X::Foo;
}

fn main() {}