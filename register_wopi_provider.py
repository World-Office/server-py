#!/usr/bin/env python3
"""
WorldOffice WOPI Provider Registrar for OpenCloud

This script registers the opencloud-docserver as an app provider via
OpenCloud's OCS API, bypassing the broken collaboration service.
"""

import requests
import time
import logging
import os
import sys

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('wopi-registrar')

# Configuration
OCIS_URL = os.environ.get('OCIS_URL', 'https://cloud.graphwiz.ai')
WOPI_URL = os.environ.get('WOPI_URL', 'https://editor.cloud.graphwiz.ai')
USER = os.environ.get('E2E_USER', 'admin')
PASS = os.environ.get('E2E_PASS', 'wo-od-2026')

session = requests.Session()


def ensure_logged_in():
    """Ensure we have a valid OCIS session"""
    global session
    
    # Check if we're already logged in
    resp = session.get(f"{OCIS_URL}/ocs/v2.php/cloud/user", verify=False)
    if resp.status_code == 200:
        try:
            import json
            data = json.loads(resp.text)
            if data.get('ocs', {}).get('meta', {}).get('status') == 'ok':
                logger.info("Already logged in")
                return
        except:
            pass
    
    # Login via OCS API
    login_url = f"{OCIS_URL}/ocs/v2.php/cloud/user/sessions"
    resp = session.post(login_url, 
                       data={'user': USER, 'password': PASS},
                       verify=False)
    logger.info(f"Login response: {resp.status_code}")
    
    # Also try web login form
    if resp.status_code != 200:
        login_page = session.get(f"{OCIS_URL}/login", verify=False)
        # Find CSRF token if needed
        import re
        token_match = re.search(r'name="requesttoken" value="([^"]+)"', login_page.text)
        token = token_match.group(1) if token_match else ''
        
        data = {'user': USER, 'password': PASS}
        if token:
            data['requesttoken'] = token
            
        resp = session.post(f"{OCIS_URL}/login", 
                           data=data,
                           allow_redirects=True,
                           verify=False)
        logger.info(f"Form login response: {resp.status_code}, final URL: {resp.url}")


def register_via_ocs():
    """
    Try to register via OCS API.
    
    OpenCloud 7.3.0 doesn't have a direct OCS API for app provider registration,
    but we might be able to use the configuration endpoints.
    """
    ensure_logged_in()
    
    # Try to check capabilities
    caps_url = f"{OCIS_URL}/ocs/v2.php/cloud/capabilities"
    resp = session.get(caps_url, verify=False)
    logger.info(f"Capabilities: {resp.status_code}")
    
    # In OCIS, app providers are typically configured via the gateway service
    # which uses the /cs3.gateway.v1beta1.GatewayAPI/OpenInApp endpoint
    
    # Let's try the most direct approach: test if /app/open works
    test_url = f"{OCIS_URL}/app/open"
    file_id = "test"  # Will fail but let's see the error
    
    # First, get a real file ID
    # List files
    files_url = f"{OCIS_URL}/ocs/v2.php/apps/files_sharing/api/v1/open_files"
    resp = session.get(files_url, verify=False)
    logger.info(f"Files endpoint: {resp.status_code}")
    
    # Actually, the /app/open endpoint expects a file_id parameter
    # Let's try to call it and see what happens
    
    # Use the known test file
    test_file_id = "2b73eee9-f6be-46ec-8db9-f3f70d8d65b5$9f3153b9-86e9-463e-9b34-89299676aa19!1f289a79-ef8e-462a-b233-18207b0ec4cb"
    
    headers = {"OCS-APIRequest": "true"}
    resp = session.post(test_url, 
                       data={'file_id': test_file_id},
                       headers=headers,
                       verify=False)
    
    logger.info(f"/app/open response: {resp.status_code}")
    logger.info(f"Response: {resp.text[:500]}")
    
    return resp.status_code == 200


def register_via_config():
    """
    Try to modify the opencloud.yaml configuration to add WorldOffice as an app provider.
    This is a more static approach but would persist across restarts.
    """
    # We need to modify the configuration inside the opencloud container
    # This requires exec into the container
    
    import subprocess
    
    config_content = """
  app_registry:
    providers:
      - name: WorldOffice
        address: https://editor.cloud.graphwiz.ai
        mimetypes:
          - application/vnd.oasis.opendocument.text
          - application/vnd.openxmlformats-officedocument.wordprocessingml.document
        app_name: WorldOffice
        icon: https://worldoffice.org/favicon.ico
        description: World Office Document Editor
        insecure: true
    default_provider: WorldOffice
"""
    
    # Try to append to the opencloud.yaml
    cmd = [
        'docker', 'exec', 'opencloud-compose-opencloud-1',
        'sh', '-c',
        'echo "*\napp_registry:\n  providers:\n    - name: WorldOffice\\n'
        '      address: https://editor.cloud.graphwiz.ai\\n'
        '      mimetypes:\\n'
        '        - application/vnd.oasis.opendocument.text\\n'
        '        - application/vnd.openxmlformats-officedocument.wordprocessingml.document\\n'
        '      app_name: WorldOffice\\n'
        '      icon: https://worldoffice.org/favicon.ico\\n'
        '      description: World Office Document Editor\\n'
        '      insecure: true\\n' 
        '    default_provider: WorldOffice' +
        ' >> /etc/opencloud/opencloud.yaml && kill -HUP 1'
    ]
    
    result = subprocess.run(cmd, capture_output=True, text=True)
    logger.info(f"Config update result: {result.returncode}")
    logger.info(f"stdout: {result.stdout}")
    logger.info(f"stderr: {result.stderr}")
    
    return result.returncode == 0


def register_via_grpc():
    """
    The most direct approach: use gRPC to call the app-registry service.
    This requires the actual protobuf definitions which are compiled into the opencloud image.
    
    As a workaround, we can use the 'grpcurl' tool which is in the opencloud container.
    """
    import subprocess
    
    # The service is:
    # cs3.app.provider.v1beta1.AppProviderAPI
    # Method: AddProvider
    
    # We need to send a protobuf-encoded message
    # This is complex without the actual .proto file
    
    # However, OpenCloud also supports setting providers via environment variables
    # in the frontend service
    
    # Let's try modifying the frontend configuration
    # The frontend uses FRONTEND_APP_HANDLER_SECURE_VIEW_APP_ADDR
    
    # But the app list comes from the gateway, not the frontend
    
    logger.info("gRPC registration requires protobuf definitions - skipping")
    return False


def main():
    logger.info("="*60)
    logger.info("WorldOffice WOPI Provider Registration")
    logger.info("="*60)
    logger.info(f"OCIS URL: {OCIS_URL}")
    logger.info(f"WOPI URL: {WOPI_URL}")
    
    # Strategy: Try multiple approaches
    
    logger.info("\n--- Approach 1: Testing /app/open endpoint ---")
    if register_via_ocs():
        logger.info("SUCCESS: /app/open is working!")
        return 0
    
    logger.info("\n--- Approach 2: Modifying opencloud.yaml ---")
    if register_via_config():
        logger.info("Configuration updated, restarting OpenCloud...")
        # Restart opencloud to pick up config changes
        import subprocess
        result = subprocess.run(
            ['docker', 'compose', '-f', '/home/weiss/opencloud-compose/docker-compose.yml',
             'restart', 'opencloud'],
            capture_output=True, text=True, cwd='/home/weiss/opencloud-compose'
        )
        logger.info(f"OpenCloud restart result: {result.returncode}")
        return 0
    
    logger.info("\n--- Approach 3: gRPC registration ---")
    if register_via_grpc():
        logger.info("gRPC registration succeeded")
        return 0
    
    logger.error("\nAll registration approaches failed!")
    logger.error("\nThe fundamental issue is that OpenCloud uses UUID-based service names")
    logger.error("in NATS (eu.opencloud.api.gateway-<uuid>) which cannot be resolved by")
    logger.error("external services expecting fixed names (eu.opencloud.api.gateway).")
    logger.error("\nTo fix this properly requires rebuilding OpenCloud Docker images with")
    logger.error("fixed service names, or using the minimal Stoic Python docserver")
    logger.error("approach documented in plan/RETHINK_WORLD_OFFICE.md")
    
    return 1


if __name__ == "__main__":
    sys.exit(main())
