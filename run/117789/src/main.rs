



auto trait Trait<P> {


}

impl<P> Trait<P> for () {
     ;
}



auto trait Bound : Copy {}

auto trait LifetimeBound : 'static {}

auto trait MyTrait {  }

fn main() {}