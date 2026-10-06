pub type Id<T> = <T as IdTrait>::T;

pub trait IdTrait {
    type T: ?Sized;
}

impl<T: ?Sized> IdTrait for T {
    type T = T;
}

#[derive(Copy, Clone)]
struct Foo;
impl Foo {
    fn method(self: Id<Self>) {
        println!("hi!");
    }
}

trait FooTrait {
    fn method2(self: Id<Self>);
}
impl FooTrait for Foo {
    fn method2(self) { // or `: Id<Self>`, makes no difference here
        println!("hi!");
    }
}

pub fn main() {
    let foo = Foo;
    foo.method(); // works
    foo.method2(); // doesn't work
    Foo::method2(foo); // works
}