trait Identity { type Ty; }
impl<'a, 'b> Identity for &'a &'b () { type Ty = &'a &'b (); }
fn test<'a, 'b>(_: <&'a &'b () as Identity>::Ty) {}
//~^ ERROR lifetime mismatch

fn main() {}