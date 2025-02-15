# Copyright 2024 Iguazio
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import re
import tempfile
from collections.abc import Iterator
from copy import deepcopy
from importlib import import_module
from typing import Optional, Union

import mlrun
from mlrun.artifacts import Artifact, ArtifactSpec
from mlrun.model import ModelObj

from ..utils import generate_artifact_uri


class DocumentLoaderSpec(ModelObj):
    """
    A class to load a document from a file path using a specified loader class.

    This class is responsible for loading documents from a given source path using a specified loader class.
    The loader class is dynamically imported and instantiated with the provided arguments. The loaded documents
    can be optionally uploaded as artifacts.

    Attributes:
        loader_class_name (str): The name of the loader class to use for loading documents.
        src_name (str): The name of the source attribute to pass to the loader class.
        kwargs (Optional[dict]): Additional keyword arguments to pass to the loader class.

    """

    _dict_fields = ["loader_class_name", "src_name", "kwargs"]

    def __init__(
        self,
        loader_class_name: str = "langchain_community.document_loaders.TextLoader",
        src_name: str = "file_path",
        kwargs: Optional[dict] = None,
    ):
        """
        Initialize the document loader.

        Args:
            loader_class_name (str): The name of the loader class to use.
            src_name (str): The source name for the document.
            kwargs (Optional[dict]): Additional keyword arguments to pass to the loader class.

        Example:
            >>> # Create a loader specification for PDF documents
            >>> loader_spec = DocumentLoaderSpec(
            ...     loader_class_name="langchain_community.document_loaders.PDFLoader",
            ...     src_name="file_path",
            ...     kwargs={"extract_images": True},
            ... )
            >>> # Create a loader instance for a specific PDF file
            >>> pdf_loader = loader_spec.make_loader("/path/to/document.pdf")
            >>> # Load the documents
            >>> documents = pdf_loader.load()

        """
        self.loader_class_name = loader_class_name
        self.src_name = src_name
        self.kwargs = kwargs

    def make_loader(self, src_path):
        module_name, class_name = self.loader_class_name.rsplit(".", 1)
        module = import_module(module_name)
        loader_class = getattr(module, class_name)
        kwargs = deepcopy(self.kwargs or {})
        kwargs[self.src_name] = src_path
        loader = loader_class(**kwargs)
        return loader


class MLRunLoader:
    """
    A factory class for creating instances of a dynamically defined document loader.

    Args:
        artifact_key (str): The key for the artifact to be logged.It can include '%%' which will be replaced
        by a hex-encoded version of the source path.
        local_path (str): The source path of the document to be loaded.
        loader_spec (DocumentLoaderSpec): Specification for the document loader.
        producer (Optional[Union[MlrunProject, str, MLClientCtx]], optional): The producer of the document
        upload (bool, optional): Flag indicating whether to upload the document.

    Returns:
        DynamicDocumentLoader: An instance of a dynamically defined subclass of BaseLoader.

    Example:
        >>> # Create a document loader specification
        >>> loader_spec = DocumentLoaderSpec(
        ...     loader_class_name="langchain_community.document_loaders.TextLoader",
        ...     src_name="file_path",
        ... )
        >>> # Create a basic loader for a single file
        >>> loader = MLRunLoader(
        ...     source_path="/path/to/document.txt",
        ...     loader_spec=loader_spec,
        ...     artifact_key="my_doc",
        ...     producer=project,
        ...     upload=True,
        ... )
        >>> documents = loader.load()
        >>> # Create a loader with auto-generated keys
        >>> loader = MLRunLoader(
        ...     source_path="/path/to/document.txt",
        ...     loader_spec=loader_spec,
        ...     artifact_key="doc%%",  # %% will be replaced with encoded path
        ...     producer=project,
        ... )
        >>> documents = loader.load()
        >>> # Use with DirectoryLoader
        >>> from langchain_community.document_loaders import DirectoryLoader
        >>> dir_loader = DirectoryLoader(
        ...     "/path/to/directory",
        ...     glob="**/*.txt",
        ...     loader_cls=MLRunLoader,
        ...     loader_kwargs={
        ...         "loader_spec": loader_spec,
        ...         "artifact_key": "doc%%",
        ...         "producer": project,
        ...         "upload": True,
        ...     },
        ... )
        >>> documents = dir_loader.load()

    """

    def __new__(
        cls,
        source_path: str,
        loader_spec: "DocumentLoaderSpec",
        artifact_key="doc%%",
        producer: Optional[Union["MlrunProject", str, "MLClientCtx"]] = None,  # noqa: F821
        upload: bool = False,
    ):
        # Dynamically import BaseLoader
        from langchain_community.document_loaders.base import BaseLoader

        class DynamicDocumentLoader(BaseLoader):
            def __init__(
                self,
                local_path,
                loader_spec,
                artifact_key,
                producer,
                upload,
            ):
                self.producer = producer
                self.artifact_key = (
                    MLRunLoader.artifact_key_instance(artifact_key, local_path)
                    if "%%" in artifact_key
                    else artifact_key
                )
                self.loader_spec = loader_spec
                self.local_path = local_path
                self.upload = upload

                # Resolve the producer
                if not self.producer:
                    self.producer = mlrun.mlconf.default_project
                if isinstance(self.producer, str):
                    self.producer = mlrun.get_or_create_project(self.producer)

            def lazy_load(self) -> Iterator["Document"]:  # noqa: F821
                artifact = self.producer.log_document(
                    key=self.artifact_key,
                    document_loader_spec=self.loader_spec,
                    local_path=self.local_path,
                    upload=self.upload,
                )
                res = artifact.to_langchain_documents()
                yield res[0]

        # Return an instance of the dynamically defined subclass
        instance = DynamicDocumentLoader(
            artifact_key=artifact_key,
            local_path=source_path,
            loader_spec=loader_spec,
            producer=producer,
            upload=upload,
        )
        return instance

    @staticmethod
    def artifact_key_instance(artifact_key: str, src_path: str) -> str:
        if "%%" in artifact_key:
            pattern = mlrun.utils.regex.artifact_key[0]
            # Convert anchored pattern (^...$) to non-anchored version for finditer
            search_pattern = pattern.strip("^$")
            result = []
            current_pos = 0

            # Find all valid sequences
            for match in re.finditer(search_pattern, src_path):
                # Add hex values for characters between matches
                for char in src_path[current_pos : match.start()]:
                    result.append(hex(ord(char))[2:].zfill(2))

                # Add the valid sequence
                result.append(match.group())
                current_pos = match.end()

            # Handle any remaining characters after the last match
            for char in src_path[current_pos:]:
                result.append(hex(ord(char))[2:].zfill(2))

            resolved_path = "".join(result)

            artifact_key = artifact_key.replace("%%", resolved_path)

        return artifact_key


class DocumentArtifact(Artifact):
    """
    A specific artifact class inheriting from generic artifact, used to maintain Document meta-data.
    """

    class DocumentArtifactSpec(ArtifactSpec):
        _dict_fields = ArtifactSpec._dict_fields + [
            "document_loader",
            "collections",
            "original_source",
        ]

        def __init__(
            self,
            *args,
            document_loader: Optional[DocumentLoaderSpec] = None,
            collections: Optional[dict] = None,
            original_source: Optional[str] = None,
            **kwargs,
        ):
            super().__init__(*args, **kwargs)
            self.document_loader = document_loader
            self.collections = collections if collections is not None else {}
            self.original_source = original_source

    kind = "document"

    METADATA_SOURCE_KEY = "source"
    METADATA_ORIGINAL_SOURCE_KEY = "original_source"
    METADATA_CHUNK_KEY = "mlrun_chunk"
    METADATA_ARTIFACT_URI_KEY = "mlrun_object_uri"
    METADATA_ARTIFACT_TARGET_PATH_KEY = "mlrun_target_path"

    def __init__(
        self,
        original_source: Optional[str] = None,
        document_loader_spec: Optional[DocumentLoaderSpec] = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.spec.document_loader = (
            document_loader_spec.to_dict()
            if document_loader_spec
            else self.spec.document_loader
        )
        self.spec.original_source = original_source or self.spec.original_source

    @property
    def spec(self) -> DocumentArtifactSpec:
        return self._spec

    @spec.setter
    def spec(self, spec):
        self._spec = self._verify_dict(
            spec, "spec", DocumentArtifact.DocumentArtifactSpec
        )

    def get_source(self):
        """Get the source URI for this artifact."""
        return generate_artifact_uri(self.metadata.project, self.spec.db_key)

    def to_langchain_documents(
        self,
        splitter: Optional["TextSplitter"] = None,  # noqa: F821
    ) -> list["Document"]:  # noqa: F821
        from langchain.schema import Document

        """
        Create LC documents from the artifact

        Args:
            splitter (Optional[TextSplitter]): A LangChain TextSplitter to split the document into chunks.

        Returns:
            list[Document]: A list of LangChain Document objects.
        """

        loader_spec = DocumentLoaderSpec.from_dict(self.spec.document_loader)
        if self.get_target_path():
            with tempfile.NamedTemporaryFile() as tmp_file:
                mlrun.datastore.store_manager.object(
                    url=self.get_target_path()
                ).download(tmp_file.name)
                loader = loader_spec.make_loader(tmp_file.name)
                documents = loader.load()
        elif self.spec.original_source:
            loader = loader_spec.make_loader(self.spec.original_source)
            documents = loader.load()
        else:
            raise ValueError(
                "No src_path or target_path provided. Cannot load document."
            )

        results = []
        idx = 0
        for document in documents:
            if splitter:
                texts = splitter.split_text(document.page_content)
            else:
                texts = [document.page_content]

            metadata = document.metadata

            metadata[self.METADATA_ORIGINAL_SOURCE_KEY] = self.spec.original_source
            metadata[self.METADATA_SOURCE_KEY] = self.get_source()
            metadata[self.METADATA_ARTIFACT_URI_KEY] = self.uri
            if self.get_target_path():
                metadata[self.METADATA_ARTIFACT_TARGET_PATH_KEY] = (
                    self.get_target_path()
                )

            for text in texts:
                metadata[self.METADATA_CHUNK_KEY] = str(idx)
                doc = Document(
                    page_content=text,
                    metadata=metadata.copy(),
                )
                results.append(doc)
                idx = idx + 1
        return results

    def collection_add(self, collection_id: str) -> None:
        """
        Add a collection ID to the artifact's collection list.

        Adds the specified collection ID to the artifact's collection mapping if it
        doesn't already exist.
        This method only modifies the client-side artifact object and does not persist
        the changes to the MLRun DB. To save the changes permanently, you must call
        project.update_artifact() after this method.

        Args:
            collection_id (str): The ID of the collection to add
        """
        if collection_id not in self.spec.collections:
            self.spec.collections[collection_id] = "1"

    def collection_remove(self, collection_id: str) -> None:
        """
        Remove a collection ID from the artifact's collection list.

        Removes the specified collection ID from the artifact's local collection mapping.
        This method only modifies the client-side artifact object and does not persist
        the changes to the MLRun DB. To save the changes permanently, you must call
        project.update_artifact() or context.update_artifact() after this method.

        Args:
            collection_id (str): The ID of the collection to remove
        """
        if collection_id in self.spec.collections:
            self.spec.collections.pop(collection_id)
