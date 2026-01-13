"""Gmail OAuth2 authentication and email fetching."""

import os
import base64
import pickle
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# If modifying these scopes, delete the token.pickle file.
SCOPES = ['https://www.googleapis.com/auth/gmail.readonly', 
          'https://www.googleapis.com/auth/gmail.modify']

TOKEN_FILE = Path(__file__).parent / "data" / "token.pickle"


def get_gmail_service():
    """
    Authenticate with Gmail and return the service object.
    
    Returns:
        A Gmail API service object, or None if authentication fails.
    """
    creds = None
    
    # Load existing token
    if TOKEN_FILE.exists():
        with open(TOKEN_FILE, 'rb') as token:
            creds = pickle.load(token)
    
    # If no valid credentials, authenticate
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                print(f"Error refreshing token: {e}")
                return None
        else:
            # Get OAuth credentials from environment
            client_id = os.environ.get("GMAIL_CLIENT_ID")
            client_secret = os.environ.get("GMAIL_CLIENT_SECRET")
            
            if not client_id or not client_secret:
                print("Gmail OAuth credentials not configured. Set GMAIL_CLIENT_ID and GMAIL_CLIENT_SECRET.")
                return None
            
            client_config = {
                "installed": {
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "redirect_uris": ["http://localhost:8080/"]
                }
            }
            
            try:
                flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
                creds = flow.run_local_server(port=8080)
            except Exception as e:
                print(f"Error during OAuth flow: {e}")
                return None
        
        # Save the credentials for the next run
        TOKEN_FILE.parent.mkdir(exist_ok=True)
        with open(TOKEN_FILE, 'wb') as token:
            pickle.dump(creds, token)
    
    try:
        service = build('gmail', 'v1', credentials=creds)
        return service
    except HttpError as error:
        print(f'An error occurred: {error}')
        return None


def fetch_cardmarket_emails(service, processed_message_ids=None):
    """
    Fetch unprocessed Cardmarket "Bitte versenden" emails.
    
    Args:
        service: Gmail API service object
        processed_message_ids: Set of already processed message IDs
        
    Returns:
        List of message objects with id, snippet, and payload
    """
    if processed_message_ids is None:
        processed_message_ids = set()
    
    try:
        # Query for Cardmarket shipping emails
        query = 'from:noreply@cardmarket.com subject:"Bitte versenden" -label:processed-tcg'
        
        results = service.users().messages().list(
            userId='me',
            q=query,
            maxResults=100
        ).execute()
        
        messages = results.get('messages', [])
        
        if not messages:
            return []
        
        # Fetch full message details
        full_messages = []
        for msg in messages:
            msg_id = msg['id']
            
            # Skip if already processed
            if msg_id in processed_message_ids:
                continue
            
            try:
                message = service.users().messages().get(
                    userId='me',
                    id=msg_id,
                    format='full'
                ).execute()
                full_messages.append(message)
            except HttpError as error:
                print(f'Error fetching message {msg_id}: {error}')
                continue
        
        return full_messages
        
    except HttpError as error:
        print(f'An error occurred fetching emails: {error}')
        return []


def mark_message_processed(service, message_id):
    """
    Mark a Gmail message as processed by adding a custom label.
    
    Args:
        service: Gmail API service object
        message_id: ID of the message to mark
        
    Returns:
        True if successful, False otherwise
    """
    try:
        # Get or create the processed-tcg label
        label_id = get_or_create_label(service, 'processed-tcg')
        
        if not label_id:
            # Fallback: just mark as read
            service.users().messages().modify(
                userId='me',
                id=message_id,
                body={'removeLabelIds': ['UNREAD']}
            ).execute()
            return True
        
        # Add the label
        service.users().messages().modify(
            userId='me',
            id=message_id,
            body={'addLabelIds': [label_id]}
        ).execute()
        
        return True
        
    except HttpError as error:
        print(f'Error marking message as processed: {error}')
        return False


def get_or_create_label(service, label_name):
    """
    Get or create a Gmail label.
    
    Args:
        service: Gmail API service object
        label_name: Name of the label
        
    Returns:
        Label ID or None if failed
    """
    try:
        # List all labels
        results = service.users().labels().list(userId='me').execute()
        labels = results.get('labels', [])
        
        # Check if label exists
        for label in labels:
            if label['name'] == label_name:
                return label['id']
        
        # Create label if it doesn't exist
        label_object = {
            'name': label_name,
            'labelListVisibility': 'labelShow',
            'messageListVisibility': 'show'
        }
        
        created_label = service.users().labels().create(
            userId='me',
            body=label_object
        ).execute()
        
        return created_label['id']
        
    except HttpError as error:
        print(f'Error managing label: {error}')
        return None


def get_email_body(message):
    """
    Extract the email body from a Gmail message.
    
    Args:
        message: Gmail message object
        
    Returns:
        Email body as string
    """
    try:
        if 'parts' in message['payload']:
            # Multipart message
            for part in message['payload']['parts']:
                if part['mimeType'] == 'text/plain':
                    data = part['body'].get('data', '')
                    if data:
                        return base64.urlsafe_b64decode(data).decode('utf-8')
                elif part['mimeType'] == 'text/html':
                    # Use HTML as fallback
                    data = part['body'].get('data', '')
                    if data:
                        return base64.urlsafe_b64decode(data).decode('utf-8')
        else:
            # Single part message
            data = message['payload']['body'].get('data', '')
            if data:
                return base64.urlsafe_b64decode(data).decode('utf-8')
    except Exception as e:
        print(f"Error extracting email body: {e}")
    
    return ""


def get_email_subject(message):
    """
    Extract the subject line from a Gmail message.
    
    Args:
        message: Gmail message object
        
    Returns:
        Email subject as string
    """
    try:
        headers = message['payload'].get('headers', [])
        for header in headers:
            if header['name'].lower() == 'subject':
                return header['value']
    except Exception as e:
        print(f"Error extracting email subject: {e}")
    
    return ""


def get_email_date(message):
    """
    Extract the date from a Gmail message.
    
    Args:
        message: Gmail message object
        
    Returns:
        Email date as ISO 8601 string, or None if not found
    """
    try:
        # Try to get internalDate (Unix timestamp in milliseconds)
        if 'internalDate' in message:
            timestamp_ms = int(message['internalDate'])
            dt = datetime.fromtimestamp(timestamp_ms / 1000.0)
            return dt.isoformat()
        
        # Fallback to Date header
        headers = message['payload'].get('headers', [])
        for header in headers:
            if header['name'].lower() == 'date':
                # Parse the date string (RFC 2822 format)
                date_str = header['value']
                dt = parsedate_to_datetime(date_str)
                return dt.isoformat()
    except Exception as e:
        print(f"Error extracting email date: {e}")
    
    return None
