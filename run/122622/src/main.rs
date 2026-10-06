trait SomeTrait {
    type SomeType<'a>;
}

#[derive(Clone)]
struct Foo<T: SomeTrait> {
    x: for<'a> fn(T::SomeType<'a>)
}

fn main() {}