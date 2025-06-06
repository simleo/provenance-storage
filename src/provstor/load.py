# Copyright © 2024-2025 CRS4
#
# This file is part of ProvStor.
#
# ProvStor is free software: you can redistribute it and/or modify it under the
# terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later
# version.
#
# ProvStor is distributed in the hope that it will be useful, but WITHOUT ANY
# WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR
# A PARTICULAR PURPOSE. See the GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with ProvStor. If not, see <https://www.gnu.org/licenses/>.


import json
import logging
import shutil
import tempfile
import zipfile
from pathlib import Path

import arcp
from minio import Minio
from rdflib import Graph
from rdflib.plugins.stores.sparqlstore import SPARQLUpdateStore
from rdflib.term import URIRef, Literal

from .config import (
    MINIO_STORE, MINIO_USER, MINIO_SECRET, MINIO_BUCKET,
    FUSEKI_BASE_URL, FUSEKI_DATASET
)
from .queries import RDE_QUERY

# anonymous read-only policy, see https://github.com/minio/minio-py/blob/88f4244fe89fb9f23de4f183bdf79524c712deaa/examples/set_bucket_policy.py#L27
MINIO_BUCKET_POLICY = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Principal": {"AWS": "*"},
            "Action": ["s3:GetBucketLocation", "s3:ListBucket"],
            "Resource": f"arn:aws:s3:::{MINIO_BUCKET}",
        },
        {
            "Effect": "Allow",
            "Principal": {"AWS": "*"},
            "Action": "s3:GetObject",
            "Resource": f"arn:aws:s3:::{MINIO_BUCKET}/*",
        },
    ],
}


def _upload_crate_to_minio(crate_path, tmp_dir):
    is_zipped = False
    if zipfile.is_zipfile(crate_path):
        is_zipped = True
        crate_name = crate_path.stem
        zipped_crate_name = crate_path.name
    else:
        if not crate_path.is_dir():
            raise ValueError("crate must be either a zip file or a directory")
        crate_name = crate_path.name
        zipped_crate_name = f"{crate_name}.zip"
    client = Minio(MINIO_STORE, MINIO_USER, MINIO_SECRET, secure=False)
    if not client.bucket_exists(MINIO_BUCKET):
        client.make_bucket(MINIO_BUCKET)
        logging.info('created bucket "%s"', MINIO_BUCKET)
        client.set_bucket_policy(MINIO_BUCKET, json.dumps(MINIO_BUCKET_POLICY))
    if is_zipped:
        archive = crate_path
    else:
        archive = shutil.make_archive(tmp_dir / crate_name, "zip", crate_path)
    client.fput_object(MINIO_BUCKET, zipped_crate_name, archive)
    crate_url = f"http://{MINIO_STORE}/{MINIO_BUCKET}/{zipped_crate_name}"
    logging.info("Crate URL: %s", crate_url)
    return crate_url, is_zipped


def load_crate_metadata(crate_path):
    tmp_dir = Path(tempfile.mkdtemp(prefix="provstor_"))
    crate_url, is_zipped = _upload_crate_to_minio(crate_path, tmp_dir)
    if is_zipped:
        with zipfile.ZipFile(crate_path, "r") as zf:
            metadata_path = Path(zf.extract("ro-crate-metadata.json", path=tmp_dir))
    else:
        metadata_path = crate_path / "ro-crate-metadata.json"
    if not metadata_path.is_file():
        raise RuntimeError(f"file {metadata_path} not found")
    store = SPARQLUpdateStore()
    query_endpoint = f"{FUSEKI_BASE_URL}/{FUSEKI_DATASET}/sparql"
    update_endpoint = f"{FUSEKI_BASE_URL}/{FUSEKI_DATASET}/update"
    store.open((query_endpoint, update_endpoint))
    graph = Graph(store, identifier=URIRef(crate_url))
    loc = arcp.arcp_location(crate_url)
    logging.info("ARCP location: %s", loc)
    graph.parse(metadata_path, publicID=loc)
    # store crate url as root data entity "url"
    qres = graph.query(RDE_QUERY)
    assert len(qres) == 1
    rde = list(qres)[0][0]
    graph.add((rde, URIRef("http://schema.org/url"), Literal(crate_url)))
    shutil.rmtree(tmp_dir)
    return crate_url
