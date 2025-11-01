#!/usr/bin/env python3
"""
pending_database_context.py - Context object for collecting database objects during XML parsing

This module provides a PendingDatabaseContext class that collects all objects
to be inserted into the database, eliminating the need for tuple passing
through the parse tree.
"""

from typing import Dict, List, Any, Optional
from models import Charity, Officer, Grant, Contractor, PoliticalContribution, Address
from database_operations import DatabaseOperations, DatabaseOperation, DatabaseOperationType


class PendingDatabaseContext:
    """
    Context object that collects all database objects during XML parsing.

    This class maintains lists of objects by type and provides methods to add
    objects and retrieve them for database insertion. Charity objects are stored
    specially since they own all other objects.
    """

    def __init__(self, xml_id: str = None, xml_content: bytes = None):
        """Initialize the context with empty collections"""
        self.xml_id = xml_id
        self.xml_content = xml_content
        self._objects: Dict[str, List[Any]] = {
            'charity': [],
            'officer': [],
            'grant': [],
            'contractor': [],
            'political_contribution': [],
            'address': []
        }
        self._charity: Optional[Charity] = None
        self._updates: List[Dict[str, Any]] = []  # For future UPDATE operations
        self._operations: List[DatabaseOperation] = []  # For generic database operations
        self.error_message: Optional[str] = None  # Error message for failed processing

    def addObjectToDatabase(self, obj: Any) -> None:
        """
        Add an object to the appropriate collection based on its type.

        Args:
            obj: The database model object to add (Charity, Officer, Grant, etc.)
        """
        obj_type = type(obj).__name__.lower()

        # Handle special case for Charity objects
        if obj_type == 'charity':
            if self._charity is not None:
                raise ValueError("Multiple Charity objects cannot be added to the same context")
            self._charity = obj
            return

        # Add to the appropriate list
        if obj_type in self._objects:
            self._objects[obj_type].append(obj)
        else:
            raise ValueError(f"Unknown object type: {obj_type}")

    def updateObject(self, obj_type: str, obj_id: str, updates: Dict[str, Any]) -> None:
        """
        Record an UPDATE operation to be performed on an existing database object.

        Args:
            obj_type: The type of object to update (e.g., 'charity', 'officer')
            obj_id: The ID of the object to update
            updates: Dictionary of field updates (column -> value)
        """
        self._updates.append({
            'type': obj_type,
            'id': obj_id,
            'updates': updates
        })

    def addOperationToDatabase(self, operation: DatabaseOperation) -> None:
        """
        Add a generic database operation to be executed.

        Args:
            operation: The DatabaseOperation to add
        """
        self._operations.append(operation)

    def getCharity(self) -> Optional[Charity]:
        """
        Get the charity object from this context.

        Returns:
            The Charity object if one exists, None otherwise
        """
        return self._charity

    def getObjectsByType(self, obj_type: str) -> List[Any]:
        """
        Get all objects of a specific type.

        Args:
            obj_type: The object type to retrieve ('charity', 'officer', etc.)

        Returns:
            List of objects of the specified type
        """
        if obj_type == 'charity':
            return [self._charity] if self._charity else []
        return self._objects.get(obj_type, [])

    def getAllObjects(self) -> Dict[str, List[Any]]:
        """
        Get all objects organized by type.

        Returns:
            Dictionary mapping object types to lists of objects
        """
        result = self._objects.copy()
        if self._charity:
            result['charity'] = [self._charity]
        return result

    def getUpdates(self) -> List[Dict[str, Any]]:
        """
        Get all recorded UPDATE operations.

        Returns:
            List of update operations
        """
        return self._updates.copy()

    def hasCharity(self) -> bool:
        """Check if this context contains a charity object"""
        return self._charity is not None

    def isEmpty(self) -> bool:
        """Check if this context contains any objects"""
        return not self.hasCharity() and all(not objs for objs in self._objects.values())

    def getObjectCounts(self) -> Dict[str, int]:
        """
        Get counts of objects by type.

        Returns:
            Dictionary mapping object types to counts
        """
        counts = {obj_type: len(objects) for obj_type, objects in self._objects.items()}
        counts['charity'] = 1 if self._charity else 0
        return counts

    def save_to_database(self, db_ops: DatabaseOperations) -> List[DatabaseOperation]:
        """
        Create database operations for all collected objects.

        PRODUCER-CONSUMER PATTERN: This method creates DatabaseOperation objects
        but does NOT execute them. The operations are returned for the consumer to handle.

        Args:
            db_ops: DatabaseOperations instance (for compatibility, but not used for direct DB operations)
            xml_id: XML file ID for operation tracking

        Returns:
            List of DatabaseOperation objects to be executed by the consumer
        """
        operations = []

        # Process in dependency order: charity first, then related objects
        if self._charity:
            operations.append(DatabaseOperation(
                DatabaseOperationType.INSERT_CHARITY,
                self._charity,
                xml_id=self.xml_id
            ))

        # Set charity_id on all related objects (will be resolved by consumer)
        charity_id = self._charity.charity_id if self._charity else None

        for officer in self._objects['officer']:
            if hasattr(officer, 'charity_id'):
                officer.charity_id = charity_id
            operations.append(DatabaseOperation(
                DatabaseOperationType.INSERT_OFFICER,
                officer,
                xml_id=self.xml_id
            ))

        for grant in self._objects['grant']:
            operations.append(DatabaseOperation(
                DatabaseOperationType.INSERT_GRANT,
                grant,
                xml_id=self.xml_id
            ))

        for contractor in self._objects['contractor']:
            operations.append(DatabaseOperation(
                DatabaseOperationType.INSERT_CONTRACTOR,
                contractor,
                xml_id=self.xml_id
            ))

        for contribution in self._objects['political_contribution']:
            operations.append(DatabaseOperation(
                DatabaseOperationType.INSERT_POLITICAL_CONTRIBUTION,
                contribution,
                xml_id=self.xml_id
            ))

        # Set owner_id on addresses (will be resolved by consumer)
        for address in self._objects['address']:
            if hasattr(address, 'address_type') and address.address_type == 'charity' and charity_id:
                address.owner_id = charity_id
            operations.append(DatabaseOperation(
                DatabaseOperationType.INSERT_ADDRESS,
                address,
                xml_id=self.xml_id
            ))

        # Add XML file update operation at the end
        from constants import CURRENT_PROCESSING_VERSION
        metadata = {
            "file_size": 0,  # Will be set by caller
            "ein": "Unknown",  # Will be set by caller
            "tax_year": None,  # Will be set by caller
            "form_type": None,  # Will be set by caller
            "error_message": self.error_message,  # Include error message if parsing failed
            "processed": True, # at least attempted
            "xml_id": self.xml_id,
            "processing_version": CURRENT_PROCESSING_VERSION
        }

        # If successful, populate metadata with actual parsed values from charity
        if not self.error_message and self._charity:
            metadata["form_type"] = self._charity.form_type
            metadata["tax_year"] = self._charity.tax_year
            metadata["ein"] = self._charity.ein
            metadata["org_type"] = self._charity.org_type
            metadata["error_message"] = "success"  # Set to "success" for successful processing

        operations.append(DatabaseOperation(
            DatabaseOperationType.XML_FILE_UPDATE,
            metadata,
            xml_id=self.xml_id
        ))

        # Add any generic operations
        operations.extend(self._operations)

        return operations

    def clear(self) -> None:
        """Clear all objects from this context"""
        self._objects = {key: [] for key in self._objects.keys()}
        self._charity = None
        self._updates = []
        self._operations = []
        self.xml_id = None
        self.xml_content = None
        self.error_message = None