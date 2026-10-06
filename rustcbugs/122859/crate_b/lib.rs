pub trait Foo {
    type FooAssoc: Bar;
}

pub trait Bar: Super<SuperAssoc: Bound> {}

pub trait Super {
    type SuperAssoc;
}

pub trait Bound: Unsatisfied {}

pub trait Unsatisfied {}