.. _composer:

1. 组合查询数据, 处理数据
====

另一个常用的场景是拼接多个异步查询, 下面的例子假设使用异步查询返回了当前用户的所有Friend 信息:

.. code-block:: python
   :linenos:
   :emphasize-lines: 13-15  

   import asyncio
   from pydantic import BaseModel
   from pydantic_resolve import Resolver

   async def search_friend(name: str):
         await asyncio.sleep(1)  # search friends of tangkikodo
         return [Friend(name="tom"), Friend(name="jerry")]

   class User(BaseModel):
      name: str
      age: int

      friends: List[Friend] = []
      async def resolve_friends(self):
         return await search_friend(self.name)

   class Friend(BaseModel):
      name: str

   async def main():
      user = User(name="tangkikodo", age=20)
      user = await Resolver().resolve(user)
      print(user.json())
      
.. code-block:: shell
   :linenos:
   :emphasize-lines: 4

   {
      "name": "tangkikodo", 
      "age": 19,
      "friends": [{"name": "tom"}, {"name": "jerry"}]
   }


postponed 计算
----

当所有的resolve 方法执行完毕之后，pydantic-resolve 会执行所有 post 方法，利用这个特性，可以对获取到的数据做后续统计计算。

.. code-block:: python
   :linenos:
   :emphasize-lines: 17-19, 21-23

   import asyncio
   from pydantic import BaseModel
   from pydantic_resolve import Resolver

   async def search_friend(name: str):
         await asyncio.sleep(1)  # search friends of tangkikodo
         return [Friend(name="tom"), Friend(name="jerry")]

   class User(BaseModel):
      name: str
      age: int

      friends: List[Friend] = []
      async def resolve_friends(self):
         return await search_friend(self.name)
      
      count: int = 0
      def post_count(self):
         return len(self.friends)

      description: str = ''
      def post_description(self):
         return f'{self.name} has {len(self.friends)} friends'

   class Friend(BaseModel):
      name: str

   async def main():
      user = User(name="tangkikodo", age=20)
      user = await Resolver().resolve(user)
      print(user.json())
      
.. code-block:: shell
   :linenos:
   :emphasize-lines: 5-6

   {
      "name": "tangkikodo", 
      "age": 19,
      "friends": [{"name": "tom"}, {"name": "jerry"}],
      "count": 2,
      "description": "tangkikodo has 2 friends"
   }