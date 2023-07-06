#!/usr/bin/env python
import inspect
import logging
from collections.abc import Iterable, MutableMapping
from collections import defaultdict
from hashlib import sha1, sha256
from pathlib import Path
from enum import Enum
from deepdiff.helper import (strings, numbers, times, unprocessed, not_hashed, add_to_frozen_set,
                             convert_item_or_items_into_set_else_none, get_doc,
                             convert_item_or_items_into_compiled_regexes_else_none,
                             get_id, type_is_subclass_of_type_group, type_in_type_group,
                             number_to_string, datetime_normalize, KEY_TO_VAL_STR, short_repr,
                             get_truncate_datetime, dict_, add_root_to_paths)
from deepdiff.base import Base
logger = logging.getLogger(__name__)

UNPROCESSED_KEY = object()

EMPTY_FROZENSET = frozenset()

INDEX_VS_ATTRIBUTE = ('[%s]', '.%s')


HASH_LOOKUP_ERR_MSG = '{} is not one of the hashed items.'


def sha256hex(obj):
    """Use Sha256 as a cryptographic hash."""
    if isinstance(obj, str):
        obj = obj.encode('utf-8')
    return sha256(obj).hexdigest()


def sha1hex(obj):
    """Use Sha1 as a cryptographic hash."""
    if isinstance(obj, str):
        obj = obj.encode('utf-8')
    return sha1(obj).hexdigest()


default_hasher = sha256hex


def combine_hashes_lists(items, prefix):
    """
    Combines lists of hashes into one hash
    This can be optimized in future.
    It needs to work with both murmur3 hashes (int) and sha256 (str)
    Although murmur3 is not used anymore.
    """
    if isinstance(prefix, bytes):
        prefix = prefix.decode('utf-8')
    hashes_bytes = b''
    for item in items:
        # In order to make sure the order of hashes in each item does not affect the hash
        # we resort them.
        hashes_bytes += (''.join(map(str, sorted(item))) + '--').encode('utf-8')
    return prefix + str(default_hasher(hashes_bytes))


class BoolObj(Enum):
    TRUE = 1
    FALSE = 0


def prepare_string_for_hashing(
        obj,
        ignore_string_type_changes=False,
        ignore_string_case=False,
        encodings=None,
        ignore_encoding_errors=False,
):
    """
    Clean type conversions
    """
    original_type = obj.__class__.__name__
    # https://docs.python.org/3/library/codecs.html#codecs.decode
    errors_mode = 'ignore' if ignore_encoding_errors else 'strict'
    if isinstance(obj, bytes):
        err = None
        encodings = ['utf-8'] if encodings is None else encodings
        encoded = False
        for encoding in encodings:
            try:
                obj = obj.decode(encoding, errors=errors_mode)
                encoded = True
                break
            except UnicodeDecodeError as er:
                err = er
        if not encoded:
            obj_decoded = obj.decode('utf-8', errors='ignore')
            start = max(err.start - 20, 0)
            start_prefix = ''
            if start > 0:
                start_prefix = '...'
            end = err.end + 20
            end_suffix = '...'
            if end >= len(obj):
                end = len(obj)
                end_suffix = ''
            raise UnicodeDecodeError(
                err.encoding,
                err.object,
                err.start,
                err.end,
                f"{err.reason} in '{start_prefix}{obj_decoded[start:end]}{end_suffix}'. Please either pass ignore_encoding_errors=True or pass the encoding via encodings=['utf-8', '...']."
            ) from None
    if not ignore_string_type_changes:
        obj = KEY_TO_VAL_STR.format(original_type, obj)
    if ignore_string_case:
        obj = obj.lower()
    return obj


doc = get_doc('deephash_doc.rst')


class DeepHash(Base):
    __doc__ = doc

    def __init__(self,
                 obj,
                 *,
                 hashes=None,
                 exclude_types=None,
                 exclude_paths=None,
                 include_paths=None,
                 exclude_regex_paths=None,
                 hasher=None,
                 ignore_repetition=True,
                 significant_digits=None,
                 truncate_datetime=None,
                 number_format_notation="f",
                 apply_hash=True,
                 ignore_type_in_groups=None,
                 ignore_string_type_changes=False,
                 ignore_numeric_type_changes=False,
                 ignore_type_subclasses=False,
                 ignore_string_case=False,
                 exclude_obj_callback=None,
                 number_to_string_func=None,
                 ignore_private_variables=True,
                 parent="root",
                 encodings=None,
                 ignore_encoding_errors=False,
                 ignore_list_order=True,
                 **kwargs):
        if kwargs:
            raise ValueError(
                ("The following parameter(s) are not valid: %s\n"
                 "The valid parameters are obj, hashes, exclude_types, significant_digits, truncate_datetime,"
                 "exclude_paths, include_paths, exclude_regex_paths, hasher, ignore_repetition, "
                 "number_format_notation, apply_hash, ignore_type_in_groups, ignore_string_type_changes, "
                 "ignore_numeric_type_changes, ignore_type_subclasses, ignore_string_case "
                 "number_to_string_func, ignore_private_variables, parent "
                 "encodings, ignore_encoding_errors") % ', '.join(kwargs.keys()))
        if isinstance(hashes, MutableMapping):
            self.hashes = hashes
        elif isinstance(hashes, DeepHash):
            self.hashes = hashes.hashes
        else:
            self.hashes = dict_()
        exclude_types = set() if exclude_types is None else set(exclude_types)
        self.exclude_types_tuple = tuple(exclude_types)  # we need tuple for checking isinstance
        self.ignore_repetition = ignore_repetition
        self.exclude_paths = add_root_to_paths(convert_item_or_items_into_set_else_none(exclude_paths))
        self.include_paths = add_root_to_paths(convert_item_or_items_into_set_else_none(include_paths))
        self.exclude_regex_paths = convert_item_or_items_into_compiled_regexes_else_none(exclude_regex_paths)
        self.hasher = default_hasher if hasher is None else hasher
        self.hashes[UNPROCESSED_KEY] = []

        self.significant_digits = self.get_significant_digits(significant_digits, ignore_numeric_type_changes)
        self.truncate_datetime = get_truncate_datetime(truncate_datetime)
        self.number_format_notation = number_format_notation
        self.ignore_type_in_groups = self.get_ignore_types_in_groups(
            ignore_type_in_groups=ignore_type_in_groups,
            ignore_string_type_changes=ignore_string_type_changes,
            ignore_numeric_type_changes=ignore_numeric_type_changes,
            ignore_type_subclasses=ignore_type_subclasses)
        self.ignore_string_type_changes = ignore_string_type_changes
        self.ignore_numeric_type_changes = ignore_numeric_type_changes
        self.ignore_string_case = ignore_string_case
        self.exclude_obj_callback = exclude_obj_callback
        # makes the hash return constant size result if true
        # the only time it should be set to False is when
        # testing the individual hash functions for different types of objects.
        self.apply_hash = apply_hash
        self.type_check_func = type_is_subclass_of_type_group if ignore_type_subclasses else type_in_type_group
        self.number_to_string = number_to_string_func or number_to_string
        self.ignore_private_variables = ignore_private_variables
        self.encodings = encodings
        self.ignore_encoding_errors = ignore_encoding_errors
        self.ignore_list_order = ignore_list_order

        self._hash(obj, parent=parent, parents_ids=frozenset({get_id(obj)}))

        if self.hashes[UNPROCESSED_KEY]:
            logger.warning("Can not hash the following items: {}.".format(self.hashes[UNPROCESSED_KEY]))
        else:
            del self.hashes[UNPROCESSED_KEY]

    sha256hex = sha256hex
    sha1hex = sha1hex

    def __getitem__(self, obj, extract_index=0):
        return self._getitem(self.hashes, obj, extract_index=extract_index)

    @staticmethod
    def _getitem(hashes, obj, extract_index=0):
        """
        extract_index is zero for hash and 1 for count and None to get them both.
        To keep it backward compatible, we only get the hash by default so it is set to zero by default.
        """

        key = obj
        if obj is True:
            key = BoolObj.TRUE
        elif obj is False:
            key = BoolObj.FALSE

        result_n_count = (None, 0)

        try:
            result_n_count = hashes[key]
        except (TypeError, KeyError):
            key = get_id(obj)
            try:
                result_n_count = hashes[key]
            except KeyError:
                raise KeyError(HASH_LOOKUP_ERR_MSG.format(obj)) from None

        if obj is UNPROCESSED_KEY:
            extract_index = None

        return result_n_count if extract_index is None else result_n_count[extract_index]

    def __contains__(self, obj):
        result = False
        try:
            result = obj in self.hashes
        except (TypeError, KeyError):
            result = False
        if not result:
            result = get_id(obj) in self.hashes
        return result

    def get(self, key, default=None, extract_index=0):
        """
        Get method for the hashes dictionary.
        It can extract the hash for a given key that is already calculated when extract_index=0
        or the count of items that went to building the object whenextract_index=1.
        """
        return self.get_key(self.hashes, key, default=default, extract_index=extract_index)

    @staticmethod
    def get_key(hashes, key, default=None, extract_index=0):
        """
        get_key method for the hashes dictionary.
        It can extract the hash for a given key that is already calculated when extract_index=0
        or the count of items that went to building the object whenextract_index=1.
        """
        try:
            result = DeepHash._getitem(hashes, key, extract_index=extract_index)
        except KeyError:
            result = default
        return result

    def _get_objects_to_hashes_dict(self, extract_index=0):
        """
        A dictionary containing only the objects to hashes,
        or a dictionary of objects to the count of items that went to build them.
        extract_index=0 for hashes and extract_index=1 for counts.
        """
        result = dict_()
        for key, value in self.hashes.items():
            if key is UNPROCESSED_KEY:
                result[key] = value
            else:
                result[key] = value[extract_index]
        return result

    def __eq__(self, other):
        if isinstance(other, DeepHash):
            return self.hashes == other.hashes
        else:
            # We only care about the hashes
            return self._get_objects_to_hashes_dict() == other

    __req__ = __eq__

    def __repr__(self):
        """
        Hide the counts since it will be confusing to see them when they are hidden everywhere else.
        """
        return short_repr(self._get_objects_to_hashes_dict(extract_index=0), max_length=500)

    __str__ = __repr__

    def __bool__(self):
        return bool(self.hashes)

    def keys(self):
        return self.hashes.keys()

    def values(self):
        return (i[0] for i in self.hashes.values())  # Just grab the item and not its count

    def items(self):
        return ((i, v[0]) for i, v in self.hashes.items())

    def _prep_obj(self, obj, parent, parents_ids=EMPTY_FROZENSET, is_namedtuple=False):
        """prepping objects"""
        original_type = type(obj) if not isinstance(obj, type) else obj

        obj_to_dict_strategies = []
        if is_namedtuple:
            obj_to_dict_strategies.append(lambda o: o._asdict())
        else:
            obj_to_dict_strategies.append(lambda o: o.__dict__)

        if hasattr(obj, "__slots__"):
            obj_to_dict_strategies.append(lambda o: {i: getattr(o, i) for i in o.__slots__})
        else:
            obj_to_dict_strategies.append(lambda o: dict(inspect.getmembers(o, lambda m: not inspect.isroutine(m))))

        for get_dict in obj_to_dict_strategies:
            try:
                d = get_dict(obj)
                break
            except AttributeError:
                pass
        else:
            self.hashes[UNPROCESSED_KEY].append(obj)
            return (unprocessed, 0)
        obj = d

        result, counts = self._prep_dict(obj, parent=parent, parents_ids=parents_ids,
                                         print_as_attribute=True, original_type=original_type)
        result = "nt{}".format(result) if is_namedtuple else "obj{}".format(result)
        return result, counts

    def _skip_this(self, obj, parent):
        skip = False
        if self.exclude_paths and parent in self.exclude_paths:
            skip = True
        if self.include_paths and parent != 'root':
            if parent not in self.include_paths:
                skip = True
                for prefix in self.include_paths:
                    if parent.startswith(prefix):
                        skip = False
                        break
        elif self.exclude_regex_paths and any(
                [exclude_regex_path.search(parent) for exclude_regex_path in self.exclude_regex_paths]):
            skip = True
        elif self.exclude_types_tuple and isinstance(obj, self.exclude_types_tuple):
            skip = True
        elif self.exclude_obj_callback and self.exclude_obj_callback(obj, parent):
            skip = True
        return skip

    def _prep_dict(self, obj, parent, parents_ids=EMPTY_FROZENSET, print_as_attribute=False, original_type=None):

        result = []
        counts = 1

        key_text = "%s{}".format(INDEX_VS_ATTRIBUTE[print_as_attribute])
        for key, item in obj.items():
            counts += 1
            # ignore private variables
            if self.ignore_private_variables and isinstance(key, str) and key.startswith('__'):
                continue
            key_formatted = "'%s'" % key if not print_as_attribute and isinstance(key, strings) else key
            key_in_report = key_text % (parent, key_formatted)

            key_hash, _ = self._hash(key, parent=key_in_report, parents_ids=parents_ids)
            if not key_hash:
                continue
            item_id = get_id(item)
            if (parents_ids and item_id in parents_ids) or self._skip_this(item, parent=key_in_report):
                continue
            parents_ids_added = add_to_frozen_set(parents_ids, item_id)
            hashed, count = self._hash(item, parent=key_in_report, parents_ids=parents_ids_added)
            hashed = KEY_TO_VAL_STR.format(key_hash, hashed)
            result.append(hashed)
            counts += count

        result.sort()
        result = ';'.join(result)
        if print_as_attribute:
            type_ = original_type or type(obj)
            type_str = type_.__name__
            for type_group in self.ignore_type_in_groups:
                if self.type_check_func(type_, type_group):
                    type_str = ','.join(map(lambda x: x.__name__, type_group))
                    break
        else:
            type_str = 'dict'
        return "{}:{{{}}}".format(type_str, result), counts

    def _prep_iterable(self, obj, parent, parents_ids=EMPTY_FROZENSET):

        counts = 1
        result = defaultdict(int)

        for i, item in enumerate(obj):
            new_parent = "{}[{}]".format(parent, i)
            if self._skip_this(item, parent=new_parent):
                continue

            item_id = get_id(item)
            if parents_ids and item_id in parents_ids:
                continue

            parents_ids_added = add_to_frozen_set(parents_ids, item_id)
            hashed, count = self._hash(item, parent=new_parent, parents_ids=parents_ids_added)
            # counting repetitions
            result[hashed] += 1
            counts += count

        if self.ignore_repetition:
            result = list(result.keys())
        else:
            result = [
                '{}|{}'.format(i, v) for i, v in result.items()
            ]

        result = map(str, result) # making sure the result items are string so join command works.
        if self.ignore_list_order:
            result = sorted(result)  
        result = ','.join(result)
        result = KEY_TO_VAL_STR.format(type(obj).__name__, result)

        return result, counts

    def _prep_bool(self, obj):
        return BoolObj.TRUE if obj else BoolObj.FALSE


    def _prep_path(self, obj):
        type_ = obj.__class__.__name__
        return KEY_TO_VAL_STR.format(type_, obj)


    def _prep_number(self, obj):
        type_ = "number" if self.ignore_numeric_type_changes else obj.__class__.__name__
        if self.significant_digits is not None:
            obj = self.number_to_string(obj, significant_digits=self.significant_digits,
                                        number_format_notation=self.number_format_notation)
        return KEY_TO_VAL_STR.format(type_, obj)

    def _prep_datetime(self, obj):
        type_ = 'datetime'
        obj = datetime_normalize(self.truncate_datetime, obj)
        return KEY_TO_VAL_STR.format(type_, obj)

    def _prep_tuple(self, obj, parent, parents_ids):
        # Checking to see if it has _fields. Which probably means it is a named
        # tuple.
        try:
            obj._asdict
        # It must be a normal tuple
        except AttributeError:
            result, counts = self._prep_iterable(obj=obj, parent=parent, parents_ids=parents_ids)
        # We assume it is a namedtuple then
        else:
            result, counts = self._prep_obj(obj, parent, parents_ids=parents_ids, is_namedtuple=True)
        return result, counts

    def _hash(self, obj, parent, parents_ids=EMPTY_FROZENSET):
        """The main diff method"""
        counts = 1

        if isinstance(obj, bool):
            obj = self._prep_bool(obj)
            result = None
        else:
            result = not_hashed
        try:
            result, counts = self.hashes[obj]
        except (TypeError, KeyError):
            pass
        else:
            return result, counts

        if self._skip_this(obj, parent):
            return None, 0

        elif obj is None:
            result = 'NONE'

        elif isinstance(obj, strings):
            result = prepare_string_for_hashing(
                obj,
                ignore_string_type_changes=self.ignore_string_type_changes,
                ignore_string_case=self.ignore_string_case,
                encodings=self.encodings,
                ignore_encoding_errors=self.ignore_encoding_errors,
            )

        elif isinstance(obj, Path):
            result = self._prep_path(obj)

        elif isinstance(obj, times):
            result = self._prep_datetime(obj)

        elif isinstance(obj, numbers):
            result = self._prep_number(obj)

        elif isinstance(obj, MutableMapping):
            result, counts = self._prep_dict(obj=obj, parent=parent, parents_ids=parents_ids)

        elif isinstance(obj, tuple):
            result, counts = self._prep_tuple(obj=obj, parent=parent, parents_ids=parents_ids)

        elif isinstance(obj, Iterable):
            result, counts = self._prep_iterable(obj=obj, parent=parent, parents_ids=parents_ids)

        elif obj == BoolObj.TRUE or obj == BoolObj.FALSE:
            result = 'bool:true' if obj is BoolObj.TRUE else 'bool:false'
        else:
            result, counts = self._prep_obj(obj=obj, parent=parent, parents_ids=parents_ids)

        if result is not_hashed:  # pragma: no cover
            self.hashes[UNPROCESSED_KEY].append(obj)

        elif result is unprocessed:
            pass

        elif self.apply_hash:
            if isinstance(obj, strings):
                result_cleaned = result
            else:
                result_cleaned = prepare_string_for_hashing(
                    result, ignore_string_type_changes=self.ignore_string_type_changes,
                    ignore_string_case=self.ignore_string_case)
            result = self.hasher(result_cleaned)

        # It is important to keep the hash of all objects.
        # The hashes will be later used for comparing the objects.
        # Object to hash when possible otherwise ObjectID to hash
        try:
            self.hashes[obj] = (result, counts)
        except TypeError:
            obj_id = get_id(obj)
            self.hashes[obj_id] = (result, counts)

        return result, counts


if __name__ == "__main__":  # pragma: no cover
    import doctest
    doctest.testmod()
