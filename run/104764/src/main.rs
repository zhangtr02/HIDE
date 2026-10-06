trait Trait { type Ty; }
impl<T> Trait for T { type Ty = (); }
fn test<T>() {
    let _: <&'static T as Trait>::Ty = ();
}

fn main() {}