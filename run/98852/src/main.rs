/// A type with an implied bound `'b: 'a`
struct Ty<'a, 'b>(&'a &'b ());

impl<'a, 'b> Ty<'a, 'b> // <- tait or inherent impl
where
    //'b: 'a, // <- fixed by an explicit bound
{
    fn f() { // <- no `Self` in signature
        Self;
        //~^ ERROR lifetime bound not satisfied
        // Cannot prove WF of `Self` because `'b: 'a` is not known to hold
    }
}

fn main() {}