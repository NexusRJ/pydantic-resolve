import asyncio
from typing import List, Optional
from pydantic import BaseModel
from pydantic_resolve import Resolver, mapper, LoaderDepend

# define loader functions
async def friends_batch_load_fn(names):
    mock_db = {
        'tangkikodo': ['tom', 'jerry'],
        'john': ['mike', 'wallace'],
        'trump': ['sam', 'jim'],
        'sally': ['sindy', 'lydia'],
    }
    return [mock_db.get(name, []) for name in names]

async def contact_batch_load_fn(names):
    mock_db = {
        'tom': 100, 'jerry':200, 'mike': 3000, 'wallace': 400, 'sam': 500,
        'jim': 600, 'sindy': 700, 'lydia': 800, 'tangkikodo': 900, 'john': 1000,
        'trump': 1200, 'sally': 1300,
    }
    result = []
    for name in names:
        n = mock_db.get(name, None)
        result.append({'number': n} if n else None)  # conver to auto mapping compatible style
    return result

# define schemas
class Contact(BaseModel):
    number: Optional[int]

class Friend(BaseModel):
    name: str

    contact: Optional[Contact] = None
    @mapper(Contact)  # auto mapping
    def resolve_contact(self, contact_loader=LoaderDepend(contact_batch_load_fn)):
        return contact_loader.load(self.name)

class User(BaseModel):
    name: str
    age: int

    greeting: str = ''
    async def resolve_greeting(self):
        await asyncio.sleep(1)
        return f"hello, i'm {self.name}, {self.age} years old."

    contact: Optional[Contact] = None
    @mapper(Contact)
    def resolve_contact(self, contact_loader=LoaderDepend(contact_batch_load_fn)):
        return contact_loader.load(self.name)
    
    friends: List[Friend] = []
    @mapper(lambda names: [Friend(name=name) for name in names])
    def resolve_friends(self, friend_loader=LoaderDepend(friends_batch_load_fn)):
        return friend_loader.load(self.name)

class Root(BaseModel):
    users: List[User] = []
    @mapper(lambda items: [User(**item) for item in items])
    def resolve_users(self):
        return [
            {"name": "tangkikodo", "age": 19}, 
            {"name": "john", "age": 20}, 
            {"name": "trump", "age": 21}, 
            {"name": "sally", "age": 22}, 
            {"name": "no man", "age": 23}, 
        ]

async def main():
    import json
    root = Root()
    root = await Resolver().resolve(root)
    dct = root.dict()
    print(json.dumps(dct, indent=4))

asyncio.run(main())