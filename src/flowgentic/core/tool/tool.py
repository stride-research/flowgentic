class Tool:
	def __init__(self, name, description, func):
		self.name = name
		self.description = description
		self.func = func  # Always a func (if its https request, then the func does that https request for example)

	def get_schema(self): ...

	async def execute(self, **kwargs):
		return await self.func(**kwargs)
