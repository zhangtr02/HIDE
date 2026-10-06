use std::marker::PhantomData;

struct Foo<'a>(PhantomData<&'a ()>);

fn foo() -> impl Fn(Foo) {
    |_| ()
}

fn main() {}