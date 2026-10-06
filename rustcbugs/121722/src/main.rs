



#[repr(C)]
struct Foo {
    _: u8,
}



struct Bar {
    _: union {
        a: u8,
    },
}




#[repr(C)]




#[main(C)]




#[repr(C)]
union C {
    _: struct {
        _: union {
            
            ,

            
            , 
            
             ,
            
            _: {
        _: struct  ,
            },
            
            _: Foo, 
            _: Bar, 
            
            _: struct ,
        },

        
          union  ,
    },
    
      union ,
}



#[repr(C)]
struct D {
    
    _: Foo,

    
    a: u8, 
    
    _: union {
        a: u8, 
    }  union ,
}




union D2 {
    
    _: Bar,

    
    a: u8, 
    
    _: union {
        a: u8, 
    }  union ,
}



#[repr(C)]
struct E {
    _: struct {
        
        ,

        
        , 
        
          union  ,
    },

    
      union ,
}


#[repr(C)]

union E2 {
    _: struct {
        
        ,

        
        , 
        
          union  ,
    },

    
      union ,
}

fn main() {}