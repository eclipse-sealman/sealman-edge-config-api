"""
Azure Blob Storage service utilities.

Provides async context managers and helpers for interacting with Azure Blob Storage
with support for SAS token and WorkloadIdentity authentication.
"""

from azure.storage.blob.aio import BlobServiceClient, ContainerClient
from azure.identity.aio import WorkloadIdentityCredential
from exceptions import APIError


class BlobContainerContext:
    """
    Async context manager for Azure Blob Storage container access with automatic cleanup.
    
    Supports dual authentication:
    - SAS token (if provided and not None/empty)
    - WorkloadIdentity (automatic fallback if sas_token is None or empty)
    
    Usage:        
        async with BlobContainerContext(STORAGE_ACCOUNT_NAME, "container-name", sas_token=SAS_TOKEN) as container:
            blob = container.get_blob_client("blob-name")
            await blob.upload_blob(data, overwrite=True)
    """
    
    def __init__(self, storage_account_name: str, container_name: str, sas_token: str | None = None):
        """
        Initialize the blob container context.
        
        Args:
            storage_account_name: The name of the Azure storage account
            container_name: Name of the blob container to connect to
            sas_token: SAS token for authentication (if None or empty, WorkloadIdentity will be used)
        """
        self.storage_account_name = storage_account_name
        self.container_name = container_name
        self.sas_token = sas_token
        self._blob_service_client: BlobServiceClient | None = None
        self._container_client: ContainerClient | None = None
        self._credential = None
    
    async def __aenter__(self) -> ContainerClient:
        """
        Enter the async context and return the container client.
        
        Returns:
            ContainerClient ready for blob operations
        """
        if self.storage_account_name is None or self.storage_account_name.strip() == "":
            raise APIError("Storage account name is not properly configured.", status_code=500)
        
        # Construct the full Azure Blob Storage URL
        storage_account_url = f"https://{self.storage_account_name}.blob.core.windows.net"
        
        try:
            if self.sas_token:
                # Use SAS token authentication
                sas_url = f"{storage_account_url}?{self.sas_token}"
                self._blob_service_client = BlobServiceClient(account_url=sas_url)
            else:
                # Fall back to WorkloadIdentity authentication
                self._credential = WorkloadIdentityCredential()
                self._blob_service_client = BlobServiceClient(
                    account_url=storage_account_url, 
                    credential=self._credential
                )
            
            self._container_client = self._blob_service_client.get_container_client(self.container_name)
        except Exception as ex:
            await self._cleanup()
            raise APIError(f"Could not create blob container client: {str(ex)}", status_code=500)
        
        return self._container_client
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Exit the async context and ensure cleanup of resources."""
        await self._cleanup()
        return False
    
    async def _cleanup(self):
        """Close the blob service client and credential if they exist."""
        if self._blob_service_client:
            await self._blob_service_client.close()
        if self._credential:
            await self._credential.close()
