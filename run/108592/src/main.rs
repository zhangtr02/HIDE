fn opaque<'a: 'a>() -> impl Sized {}
fn assert_static<T: 'static>(_: T) {}

fn test() {
    let closure = |_| {
        assert_static(opaque());
        //~^ ERROR the opaque type may not live long enough
    };
    closure(&opaque());
}

fn main() {}