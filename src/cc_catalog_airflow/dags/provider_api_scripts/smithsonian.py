"""
Content Provider:  Smithsonian

ETL Process:       Use the API to identify all CC licensed images.

Output:            TSV file containing the images and the respective
                   meta-data.

Notes:             None
"""
from datetime import datetime
import json
import logging
import os

from common.storage import image
from common import requester
logger = logging.getLogger(__name__)

API_KEY = os.getenv('DATA_GOV_API_KEY')
DELAY = 5.0
HASH_PREFIX_LENGTH = 2
LIMIT = 1000  # number of rows to pull at once
API_ROOT = 'https://api.si.edu/openaccess/api/v1.0/'
SEARCH_ENDPOINT = API_ROOT + 'search'
UNITS_ENDPOINT = API_ROOT + 'terms/unit_code'
PROVIDER = 'smithsonian'
ZERO_URL = 'https://creativecommons.org/publicdomain/zero/1.0/'
DEFAULT_PARAMS = {
    'api_key': API_KEY,
    'rows': LIMIT
}
RETRIES = 3
# CREATOR_TYPES should have lower-case strings as keys, and integers as values.
# The integers given the preference order of the different creator types, with
# lower being more preferred. No preference is implied between two creator
# types with the same integer value.
CREATOR_TYPES = {
    'artist': 0,
    'artist/maker': 0,
    'attributed to': 0,
    'author': 0,
    'created_by': 0,
    'creator': 0,
    'model maker': 0,
    'photographer': 0,
    'photograph by': 0,
    'written by': 0,

    'architect': 1,
    'designer': 1,

    'compiled by': 2,
    'engraver': 2,
    'etcher': 2,
    'maker': 2,
    'silversmith': 2,

    'print maker': 3,
    'after': 3,
    'inventor': 0,

    'manufactured by': 4,
    'manufacturer': 4,
    'published by': 4,
    'publisher': 4,

    'patentee': 5,
}

image_store = image.ImageStore(provider=PROVIDER)
delayed_requester = requester.DelayedRequester(delay=DELAY)


def main():
    logging.basicConfig(
        format='%(asctime)s - %(name)s - %(levelname)s:  %(message)s',
        level=logging.INFO
    )
    for hash_prefix in _get_hash_prefixes(HASH_PREFIX_LENGTH):
        total_rows = _process_hash_prefix(hash_prefix)
        logger.info(f'Total rows for {hash_prefix}:  {total_rows}')
    total_images = image_store.commit()
    logger.info(f'Total images:  {total_images}')


def gather_samples(
        units_endpoint=UNITS_ENDPOINT,
        default_params=DEFAULT_PARAMS,
        target_dir='/tmp'
):
    """
    Gather random samples of the rows from each 'unit' at the SI.

    These units are treated separately since they have somewhat different data
    formats.
    """
    logging.basicConfig(
        format='%(asctime)s - %(name)s - %(levelname)s:  %(message)s',
        level=logging.INFO
    )
    now_str = datetime.strftime(datetime.now(), '%Y%m%d%H%M%S')
    sample_dir = os.path.join(target_dir, f'si_samples_{now_str}')
    logger.info(f'Creating sample_dir {sample_dir}')
    os.mkdir(sample_dir)
    unit_code_json = delayed_requester.get_response_json(
        units_endpoint,
        query_params=default_params
    )
    unit_code_list = unit_code_json.get('response', {}).get('terms', [])
    logger.info(f'found unit codes: {unit_code_list}')
    for unit in unit_code_list:
        _gather_unit_sample(unit, sample_dir)


def _gather_unit_sample(
        unit,
        sample_dir,
        retries=RETRIES,
        endpoint=SEARCH_ENDPOINT
):
    logger.info(f'gathering sample for unit {unit}')
    for hash_prefix in [None, 'a', 'aa', 'aaa', 'aaaa', 'aaaaa']:
        query_params = _build_query_params(
            0,
            hash_prefix=hash_prefix,
            unit_code=unit
        )
        response_json = delayed_requester.get_response_json(
            endpoint,
            retries=retries,
            query_params=query_params
        )
        if response_json is None:
            logger.warning(f'response_json is NoneType for {unit}')
            break
        elif response_json['response']['rowCount'] == 0:
            logger.info(
                f'No rows found for unit_code {unit} with hash {hash_prefix}'
            )
            break
        elif response_json['response']['rowCount'] > 10000:
            logger.info(
                f'Too many rows:  {response_json["response"]["rowCount"]}'
            )
        else:
            total_rows = response_json['response']['rowCount']
            saved_rows = min(total_rows, 1000)
            logger.info(f'Saving {saved_rows} of {total_rows} rows')
            with open(os.path.join(sample_dir, f'{unit}.json'), 'w') as f:
                f.write(json.dumps(response_json, indent=2))
            break


def _get_hash_prefixes(prefix_length):
    max_prefix = 'f' * prefix_length
    format_string = f'0{prefix_length}x'
    for h in range(int(max_prefix, 16) + 1):
        yield format(h, format_string)


def _process_hash_prefix(
        hash_prefix,
        endpoint=SEARCH_ENDPOINT,
        limit=LIMIT,
        retries=RETRIES
):
    logger.info(f'Processing hash_prefix:  {hash_prefix}')
    total_images = 0
    row_offset = 0
    total_rows = 1
    while row_offset < total_rows:
        logger.debug(f'Row offset:  {row_offset}')
        query_params = _build_query_params(row_offset, hash_prefix=hash_prefix)
        response_json = delayed_requester.get_response_json(
            endpoint,
            retries=retries,
            query_params=query_params
        )
        if response_json is None:
            logger.warning('response_json is None!  Continuing...')
        else:
            new_total = _process_response_json(response_json)
            total_images = new_total if new_total is not None else total_images
            logger.info(f'Total images so far:  {total_images}')
            total_rows = response_json.get('response', {}).get('rowCount', 0)
        row_offset += limit
    return total_rows


def _build_query_params(
        row_offset,
        hash_prefix=None,
        default_params=DEFAULT_PARAMS,
        unit_code=None
):
    query_params = default_params.copy()
    query_string = 'online_media_type:Images AND media_usage:CC0'
    if hash_prefix is not None:
        query_string += f' AND hash:{hash_prefix}*'
    if unit_code is not None:
        query_string += f' AND unit_code:{unit_code}'
    query_params.update(q=query_string, start=row_offset)
    return query_params


def _process_response_json(response_json):
    logger.debug('processing response')
    total_images = None
    rows = response_json.get('response', {}).get('rows', [])
    for row in rows:
        content = row.get('content', {})
        descriptive_non_repeating = content.get('descriptiveNonRepeating', {})
        indexed_structured = content.get('indexedStructured', {})
        freetext = content.get('freetext')

        title = row.get('title')
        landing_url = _get_foreign_landing_url(descriptive_non_repeating)
        creator = _get_creator(indexed_structured, freetext)
        meta_data = _extract_meta_data(descriptive_non_repeating, freetext)
        tags = _extract_tags(indexed_structured)

        image_list = (
            descriptive_non_repeating
            .get('online_media', {})
            .get('media')
        )
        if image_list is not None:
            total_images = _process_image_list(
                image_list,
                landing_url,
                title,
                creator,
                meta_data,
                tags
            )
    return total_images


def _get_foreign_landing_url(dnr_dict):
    foreign_landing_url = dnr_dict.get('record_link')
    if foreign_landing_url is None:
        foreign_landing_url = dnr_dict.get('guid')

    return foreign_landing_url


def _get_creator(
        indexed_structured,
        freetext,
        creator_types=CREATOR_TYPES
):
    ordered_freetext_creator_objects = sorted(
        [
            i for i in freetext.get('name', [])
            if type(i) == dict
            and i.get('label', '').lower() in creator_types
            and i.get('content')
        ],
        key=lambda x: creator_types[x['label'].lower()]
    )
    freetext_creator_generator = (
        c['content'] for c in ordered_freetext_creator_objects
    )
    indexed_structured_creator_generator = (
        i['content'] for i in indexed_structured.get('name', [])
        if type(i) == dict
        and i.get('type', '').lower() == 'personal_main'
        and i.get('content')
    )

    creator = next(freetext_creator_generator, None)
    if creator is None:
        logger.debug(f'No creator found in freetext:  {freetext}')
        creator = next(indexed_structured_creator_generator, None)
    if creator is None:
        logger.debug(
            f'No creator found in indexed_structured:  {indexed_structured}'
        )
    return creator


def _extract_meta_data(descriptive_non_repeating, freetext):
    description = ''
    label_texts = ''
    notes = freetext.get('notes', [])

    for note in notes:
        if note.get('label') == 'Description':
            description += ' ' + note.get('content', '')
        elif note.get('label') == 'Summary':
            description += ' ' + note.get('content', '')
        elif note.get('label') == 'Caption':
            description += ' ' + note.get('content', '')
        elif note.get('label') == 'Label Text':
            label_texts += ' ' + note.get('content', '')

    meta_data = {
        'unit_code': descriptive_non_repeating.get('unit_code'),
        'data_source': descriptive_non_repeating.get('data_source')
    }
    if description:
        meta_data.update(description=description)
    if label_texts:
        meta_data.update(label_texts=label_texts)

    return meta_data


def _extract_tags(indexed_structured):
    tags = (
        indexed_structured.get('date', [])
        + indexed_structured.get('object_type', [])
        + indexed_structured.get('topic', [])
        + indexed_structured.get('place', [])
    )
    return tags if tags else None


def _process_image_list(
        image_list,
        foreign_landing_url,
        title,
        creator,
        meta_data,
        tags,
        license_url=ZERO_URL
):
    total_images = None
    for image_data in image_list:
        if (
                image_data.get('type') == 'Images'
                and image_data.get('usage', {}).get('access') == 'CC0'
        ):
            total_images = image_store.add_item(
                foreign_landing_url=foreign_landing_url,
                image_url=image_data.get('content'),
                thumbnail_url=image_data.get('thumbnail'),
                license_url=license_url,
                foreign_identifier=image_data.get('idsId'),
                title=title,
                creator=creator,
                meta_data=meta_data,
                raw_tags=tags
            )
    return total_images


if __name__ == '__main__':
    main()