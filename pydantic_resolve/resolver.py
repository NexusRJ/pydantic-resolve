import asyncio
import contextvars
import warnings
from inspect import iscoroutine
from typing import TypeVar, Dict, Type, Callable

import pydantic_resolve.utils.conversion as conversion_util
from .exceptions import MissingAnnotationError
from typing import Any, Optional
from pydantic_resolve import analysis
from aiodataloader import DataLoader
from types import MappingProxyType
import pydantic_resolve.constant as const
import pydantic_resolve.utils.class_util as class_util


T = TypeVar("T")

class Resolver:
    def __init__(
            self,
            loader_filters: Optional[Dict[Any, Dict[str, Any]]] = None,  # deprecated
            loader_params: Optional[Dict[Any, Dict[str, Any]]] = None,
            global_loader_filter: Optional[Dict[str, Any]] = None,  # deprecated
            global_loader_param: Optional[Dict[str, Any]] = None,
            loader_instances: Optional[Dict[Any, Any]] = None,
            ensure_type=False,
            context: Optional[Dict[str, Any]] = None):
        self.loader_instance_cache = {}

        self.ancestor_vars = {}
        self.collector_contextvars = {}
        self.parent_contextvars = {}

        # for dataloader which has class attributes, you can assign the value at here
        if loader_filters:
            warnings.warn('loader_filters is deprecated, use loader_params instead.', DeprecationWarning)
            self.loader_params = loader_filters
        else:
            self.loader_params = loader_params or {}

        # keys in global_loader_filter are mutually exclusive with key-value pairs in loader_filters
        # eg: Resolver(global_loader_filter={'key_a': 1}, loader_filters={'key_a': 1}) will raise exception
        if global_loader_filter:
            warnings.warn('global_loader_filter is deprecated, use global_loader_param instead.', DeprecationWarning)
            self.global_loader_param = global_loader_filter or {}
        else:
            self.global_loader_param = global_loader_param or {}

        # now you can pass your loader instance, Resolver will check `isinstance``
        if loader_instances and self._validate_loader_instance(loader_instances):
            self.loader_instances = loader_instances
        else:
            self.loader_instances = {}

        self.ensure_type = ensure_type
        self.context = MappingProxyType(context) if context else None
        self.metadata = {}
        self.object_collect_alias_map_store = {}

    def _validate_loader_instance(self, loader_instances: Dict[Any, Any]):
        for cls, loader in loader_instances.items():
            if not issubclass(cls, DataLoader):
                raise AttributeError(f'{cls.__name__} must be subclass of DataLoader')
            if not isinstance(loader, cls):
                raise AttributeError(f'{loader.__name__} is not instance of {cls.__name__}')
        return True
    
    def _prepare_collectors(self, node: object, kls: Type):
        alias_map = analysis.generate_alias_map_with_cloned_collector(kls, self.metadata)
        if alias_map:
            # store for later post methods
            self.object_collect_alias_map_store[id(node)] = alias_map  

            # expose to descendant
            for alias_name, sign_collector_kv in alias_map.items():
                if not self.collector_contextvars.get(alias_name):
                    self.collector_contextvars[alias_name] = contextvars.ContextVar(alias_name, default={})
                
                current_pair = self.collector_contextvars[alias_name].get()
                if set(sign_collector_kv.keys()) - set(current_pair.keys()):  # update only when new sign is found
                    updated_pair = {**current_pair, **sign_collector_kv}
                    self.collector_contextvars[alias_name].set(updated_pair)

    def _add_values_into_collectors(self, node: object, kls: Type):
        for field, alias in analysis.iter_over_collectable_fields(kls, self.metadata):
            # handle two kinds of scenarios
            # {'name': ('collector_a', 'collector_b')}
            # {'name': 'collector_a'}
            alias_list = alias if isinstance(alias, (tuple, list)) else (alias,)

            for alias in alias_list:
                for _, instance in self.collector_contextvars[alias].get().items():
                    val = [getattr(node, f) for f in field]\
                        if isinstance(field, tuple) else getattr(node, field)
                    instance.add(val)
    
    def _add_parent(self, node: object):
        if not self.parent_contextvars.get('parent'):
            self.parent_contextvars['parent'] = contextvars.ContextVar('parent')
        self.parent_contextvars['parent'].set(node)

    def _add_expose_fields(self, node: object):
        expose_dict: Optional[dict] = getattr(node, const.EXPOSE_TO_DESCENDANT, None)
        if expose_dict:
            for field, alias in expose_dict.items():  # eg: {'name': 'bar_name'}
                if not self.ancestor_vars.get(alias):
                    self.ancestor_vars[alias] = contextvars.ContextVar(alias)

                try:
                    val = getattr(node, field)
                except AttributeError:
                    raise AttributeError(f'{field} does not existed')

                self.ancestor_vars[alias].set(val)

    def _prepare_ancestor_context(self):
        return {k: v.get() for k, v in self.ancestor_vars.items()}

    def _execute_resolver_method(
            self,
            kls: Type,
            field: str,
            method: Callable):
        params = {}
        resolve_param = analysis.get_resolve_param(kls, field, self.metadata)
        if resolve_param['context']:
            params['context'] = self.context
        if resolve_param['ancestor_context']:
            params['ancestor_context'] = self._prepare_ancestor_context()
        if resolve_param['parent']:
            params['parent'] = self.parent_contextvars['parent'].get()
        
        for loader in resolve_param['dataloaders']:
            cache_key = loader['path']
            loader_instance = self.loader_instance_cache[cache_key]
            params[loader['param']] = loader_instance

        return method(**params)
    
    def _execute_post_method(
            self,
            node: object,
            kls: Type,
            kls_path: str,
            post_field: str,
            method: Callable):
        params = {}
        post_param = analysis.get_post_params(kls, post_field , self.metadata)

        if post_param['context']:
            params['context'] = self.context
        if post_param['ancestor_context']:
            params['ancestor_context'] = self._prepare_ancestor_context()
        if post_param['parent']:
            params['parent'] = self.parent_contextvars['parent'].get()

        alias_map = self.object_collect_alias_map_store.get(id(node), {})
        if alias_map:
            for collector in post_param['collectors']:
                signature = analysis.get_collector_sign(kls_path, collector)
                alias, param = collector['alias'], collector['param']
                params[param] = alias_map[alias][signature]
        
        return method(**params)

    def _execute_post_default_handler(self, node, kls, kls_path, method):
        params = {}
        post_default_param = analysis.get_post_default_handler_params(kls, self.metadata)

        if post_default_param is None:
            return

        if post_default_param['context']:
            params['context'] = self.context
        if post_default_param['ancestor_context']:
            params['ancestor_context'] = self._prepare_ancestor_context()
        if post_default_param['parent']:
            params['parent'] = self.parent_contextvars['parent'].get()

        alias_map = self.object_collect_alias_map_store.get(id(node), {})
        if alias_map:
            for collector in post_default_param['collectors']:
                alias, param = collector['alias'], collector['param']
                signature = (kls_path, const.POST_DEFAULT_HANDLER, param)
                params[param] = alias_map[alias][signature]

        return method(**params)

    async def _resolve_resolve_method_field(
            self, 
            node: object, 
            kls: Type,
            field: str,
            trim_field: str,
            method: Callable):
        if self.ensure_type:
            if not method.__annotations__:
                raise MissingAnnotationError(f'{field}: return annotation is required')

        val = self._execute_resolver_method(kls, field, method)
        while iscoroutine(val) or asyncio.isfuture(val):
            val = await val

        if not getattr(method, const.HAS_MAPPER_FUNCTION, False):  # defined in util.mapper
            val = conversion_util.try_parse_data_to_target_field_type(node, trim_field, val)

        # continue dive deeper
        val = await self._resolve(val, node)

        setattr(node, trim_field, val)

    async def _resolve(self, node: T, parent: object) -> T:
        if isinstance(node, (list, tuple)):
            # list should not play as parent, use original parent.
            await asyncio.gather(*[self._resolve(t, parent) for t in node])

        if not analysis.is_acceptable_instance(node):  # skip
            return node

        kls = node.__class__
        kls_path = class_util.get_kls_full_path(kls)

        self._prepare_collectors(node, kls)
        self._add_expose_fields(node)
        self._add_parent(parent)

        tasks = []

        # traversal and fetching data by resolve methods
        resolve_list, attribute_list = analysis.iter_over_object_resolvers_and_acceptable_fields(node, kls, self.metadata)
        for field, resolve_trim_field, method in resolve_list:
            tasks.append(self._resolve_resolve_method_field(node, kls, field, resolve_trim_field, method))

        for field, attr_object in attribute_list:
            tasks.append(self._resolve(attr_object, node))

        await asyncio.gather(*tasks)

        # reverse traversal and run post methods
        for post_field, post_trim_field in analysis.iter_over_object_post_methods(kls, self.metadata):
            post_method = getattr(node, post_field)
            result = self._execute_post_method(node, kls, kls_path, post_field, post_method)

            # although post method support async, but not recommended to use 
            while iscoroutine(result) or asyncio.isfuture(result):
                result = await result
                
            result = conversion_util.try_parse_data_to_target_field_type(node, post_trim_field, result)
            setattr(node, post_trim_field, result)

        default_post_method = getattr(node, const.POST_DEFAULT_HANDLER, None)
        if default_post_method:
            self._execute_post_default_handler(node, kls, kls_path, default_post_method)

        # collect after all done
        self._add_values_into_collectors(node, kls)
        return node


    async def resolve(self, node: T) -> T:
        if isinstance(node, list) and node == []: return node

        root_class = class_util.get_class(node)
        metadata = analysis.scan_and_store_metadata(root_class)
        self.metadata = analysis.convert_metadata_key_as_kls(metadata)

        self.loader_instance_cache = analysis.validate_and_create_loader_instance(
            self.loader_params,
            self.global_loader_param,
            self.loader_instances,
            self.metadata)
        
        has_context = analysis.has_context(self.metadata)
        if has_context and self.context is None:
            raise AttributeError('context is missing')
            
        await self._resolve(node, None)
        return node