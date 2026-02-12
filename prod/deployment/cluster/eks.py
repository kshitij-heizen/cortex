#!/usr/bin/env python3

import sys
import yaml
import subprocess
import logging
import argparse
from datetime import datetime
from pathlib import Path

try:
    from colorama import Fore, Style, init
    init(autoreset=True)
except ImportError:
    print("Warning: colorama not installed. Install with: pip install colorama")
    class Fore:
        RED = GREEN = YELLOW = BLUE = CYAN = MAGENTA = WHITE = ""
    class Style:
        BRIGHT = RESET_ALL = ""

SCRIPT_DIR = Path(__file__).parent
BASE_CONFIG_PATH = SCRIPT_DIR / "example-config.yaml"
OUTPUT_PATH = SCRIPT_DIR / "cortex.eks.yaml"
LOG_FILE = SCRIPT_DIR / f"eks-setup-{datetime.now().strftime('%Y%m%d-%H%M%S')}.log"
DEFAULT_KARPENTER_TAG_KEY = "cortex/karpenter-discovery"
DEFAULT_KARPENTER_VERSION = "1.0.6"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

def print_header(message):
    """Print a formatted header message."""
    print(f"\n{Fore.CYAN}{Style.BRIGHT}{'='*60}")
    print(f"{Fore.CYAN}{Style.BRIGHT}{message.center(60)}")
    print(f"{Fore.CYAN}{Style.BRIGHT}{'='*60}{Style.RESET_ALL}\n")

def print_success(message):
    """Print a success message."""
    print(f"{Fore.GREEN}✓ {message}{Style.RESET_ALL}")
    logger.info(message)

def print_error(message):
    """Print an error message."""
    print(f"{Fore.RED}✗ {message}{Style.RESET_ALL}")
    logger.error(message)

def print_warning(message):
    """Print a warning message."""
    print(f"{Fore.YELLOW}⚠ {message}{Style.RESET_ALL}")
    logger.warning(message)

def print_info(message):
    """Print an info message."""
    print(f"{Fore.BLUE}ℹ {message}{Style.RESET_ALL}")
    logger.info(message)

def check_file_exists(file_path):
    """Check if a file exists."""
    path = Path(file_path)
    if not path.exists():
        print_error(f"File does not exist: {file_path}")
        return False
    if not path.is_file():
        print_error(f"Path is not a file: {file_path}")
        return False
    print_success(f"Found file: {file_path}")
    return True

def check_prerequisites():
    """Check if required tools are installed."""
    print_info("Checking prerequisites...")
    
    required_tools = {
        'eksctl': 'eksctl version',
        'kubectl': 'kubectl version --client',
        'aws': 'aws --version'
    }
    
    missing_tools = []
    
    for tool, cmd in required_tools.items():
        try:
            result = subprocess.run(
                cmd.split(),
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                print_success(f"{tool} is installed")
            else:
                missing_tools.append(tool)
                print_error(f"{tool} is not installed or not working")
        except (subprocess.TimeoutExpired, FileNotFoundError):
            missing_tools.append(tool)
            print_error(f"{tool} is not installed")
    
    if missing_tools:
        print_error(f"Missing required tools: {', '.join(missing_tools)}")
        print_info("Please install the missing tools before continuing.")
        return False
    
    print_success("All prerequisites are met")
    return True

def check_aws_credentials():
    """Check if AWS credentials are configured."""
    print_info("Checking AWS credentials...")
    try:
        result = subprocess.run(
            ['aws', 'sts', 'get-caller-identity'],
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode == 0:
            print_success("AWS credentials are configured")
            return True
        else:
            print_error("AWS credentials are not configured or invalid")
            print_info("Run 'aws configure' to set up your credentials")
            return False
    except Exception as e:
        print_error(f"Failed to check AWS credentials: {e}")
        return False

def get_aws_account_id():
    try:
        result = subprocess.run(
            ['aws', 'sts', 'get-caller-identity', '--query', 'Account', '--output', 'text'],
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode == 0:
            return result.stdout.strip()
        return None
    except Exception:
        return None

def deploy_karpenter_cloudformation(cluster_name, karpenter_version, region):
    print_header("Deploying Karpenter IAM Resources")
    print_info("Downloading Karpenter CloudFormation template...")
    
    import tempfile
    
    cf_url = f"https://raw.githubusercontent.com/aws/karpenter-provider-aws/v{karpenter_version}/website/content/en/preview/getting-started/getting-started-with-karpenter/cloudformation.yaml"
    
    try:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as tmp:
            result = subprocess.run(
                ['curl', '-fsSL', cf_url],
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode != 0:
                print_error(f"Failed to download CloudFormation template: {result.stderr}")
                return False
            
            tmp.write(result.stdout)
            tmp_path = tmp.name
        
        print_success("Downloaded CloudFormation template")
        
        print_info(f"Deploying CloudFormation stack: Karpenter-{cluster_name}")
        print_info("This creates IAM roles and policies for Karpenter...")
        
        deploy_result = subprocess.run(
            [
                'aws', 'cloudformation', 'deploy',
                '--stack-name', f'Karpenter-{cluster_name}',
                '--template-file', tmp_path,
                '--capabilities', 'CAPABILITY_NAMED_IAM',
                '--parameter-overrides', f'ClusterName={cluster_name}',
                '--region', region
            ],
            capture_output=True,
            text=True,
            timeout=300
        )
        
        import os
        os.unlink(tmp_path)
        
        if deploy_result.returncode == 0:
            print_success("Karpenter IAM resources deployed successfully")
            return True
        else:
            if "No changes to deploy" in deploy_result.stderr or "No changes to deploy" in deploy_result.stdout:
                print_success("Karpenter IAM resources already exist (no changes needed)")
                return True
            print_error(f"CloudFormation deployment failed: {deploy_result.stderr}")
            return False
            
    except subprocess.TimeoutExpired:
        print_error("CloudFormation deployment timed out")
        return False
    except Exception as e:
        print_error(f"Failed to deploy CloudFormation stack: {e}")
        return False

def get_subnet_tags(subnet_id, region):
    """Get tags for a specific subnet."""
    try:
        result = subprocess.run(
            ['aws', 'ec2', 'describe-tags',
             '--filters', f'Name=resource-id,Values={subnet_id}',
             '--region', region,
             '--output', 'json'],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if result.returncode == 0:
            import json
            data = json.loads(result.stdout)
            tags = {tag['Key']: tag['Value'] for tag in data.get('Tags', [])}
            return tags
        else:
            print_error(f"Failed to get tags for subnet {subnet_id}: {result.stderr}")
            return None
    except subprocess.TimeoutExpired:
        print_error(f"Timeout while getting tags for subnet {subnet_id}")
        return None
    except Exception as e:
        print_error(f"Error getting tags for subnet {subnet_id}: {e}")
        return None

def check_subnet_has_karpenter_tag(subnet_id, cluster_name, region, tag_key):
    """Check if a subnet has the Karpenter discovery tag."""
    tags = get_subnet_tags(subnet_id, region)
    if tags is None:
        return False
    
    if tag_key in tags and tags[tag_key] == cluster_name:
        return True
    return False

def tag_subnet_for_karpenter(subnet_id, cluster_name, region, tag_key):
    """Add Karpenter discovery tag to a subnet."""
    try:
        result = subprocess.run(
            ['aws', 'ec2', 'create-tags',
             '--resources', subnet_id,
             '--tags', f'Key={tag_key},Value={cluster_name}',
             '--region', region],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if result.returncode == 0:
            return True
        else:
            print_error(f"Failed to tag subnet {subnet_id}: {result.stderr}")
            return False
    except subprocess.TimeoutExpired:
        print_error(f"Timeout while tagging subnet {subnet_id}")
        return False
    except Exception as e:
        print_error(f"Error tagging subnet {subnet_id}: {e}")
        return False

def get_karpenter_tag_key(cluster_input):
    """Get the Karpenter discovery tag key from config or use default."""
    tag_key = None
    
    if "karpenter" in cluster_input and "tag-key" in cluster_input["karpenter"]:
        tag_key = cluster_input["karpenter"]["tag-key"]
        if tag_key and isinstance(tag_key, str):
            print_success(f"Using Karpenter tag key from config: {tag_key}")
            return tag_key
        else:
            print_warning("Invalid karpenter.tag-key in config, using default")
    
    print_info(f"Using default Karpenter tag key: {Fore.WHITE}{DEFAULT_KARPENTER_TAG_KEY}{Style.RESET_ALL}")
    print_info("To customize, add 'karpenter.tag-key' to your values file")
    return DEFAULT_KARPENTER_TAG_KEY

def display_subnet_tags_table(subnet_data):
    """Display subnet tags in a formatted table."""
    print(f"\n{Fore.CYAN}{Style.BRIGHT}Current Subnet Tags:{Style.RESET_ALL}")
    print(f"{Fore.CYAN}{'='*100}{Style.RESET_ALL}")
    
    header = f"{Fore.WHITE}{Style.BRIGHT}{'Subnet ID':<25} {'AZ':<15} {'Type':<10} {'Tags':<50}{Style.RESET_ALL}"
    print(header)
    print(f"{Fore.CYAN}{'-'*100}{Style.RESET_ALL}")
    
    for subnet_id, az, subnet_type, tags in subnet_data:
        if tags:
            tags_str = ", ".join([f"{k}={v}" for k, v in list(tags.items())[:3]])
            if len(tags) > 3:
                tags_str += f" (+{len(tags)-3} more)"
        else:
            tags_str = f"{Fore.YELLOW}(no tags){Style.RESET_ALL}"
        
        print(f"{subnet_id:<25} {az:<15} {subnet_type:<10} {tags_str}")
    
    print(f"{Fore.CYAN}{'='*100}{Style.RESET_ALL}\n")

def ensure_subnets_tagged(cluster_input, cluster_name, region, auto_confirm=False):
    """Ensure all subnets have the Karpenter discovery tag."""
    print_header("Karpenter Subnet Tagging")
    
    tag_key = get_karpenter_tag_key(cluster_input)
    print_info(f"Subnets will be tagged with: {Fore.WHITE}{tag_key}={cluster_name}{Style.RESET_ALL}")
    
    all_subnets = []
    
    if "vpc" in cluster_input and "subnets" in cluster_input["vpc"]:
        subnets_config = cluster_input["vpc"]["subnets"]
        
        if "private" in subnets_config:
            for az, subnet_info in subnets_config["private"].items():
                subnet_id = subnet_info.get("id")
                if subnet_id:
                    all_subnets.append((subnet_id, az, "private"))
        
        if "public" in subnets_config:
            for az, subnet_info in subnets_config["public"].items():
                subnet_id = subnet_info.get("id")
                if subnet_id:
                    all_subnets.append((subnet_id, az, "public"))
    
    if not all_subnets:
        print_warning("No subnets found in configuration")
        return True
    
    print_info(f"Found {len(all_subnets)} subnet(s) to check")
    print_info("Fetching current tags for all subnets...")
    
    subnet_data_with_tags = []
    subnets_to_tag = []
    subnets_already_tagged = []
    subnets_failed = []
    
    for subnet_id, az, subnet_type in all_subnets:
        tags = get_subnet_tags(subnet_id, region)
        subnet_data_with_tags.append((subnet_id, az, subnet_type, tags))
        
        if check_subnet_has_karpenter_tag(subnet_id, cluster_name, region, tag_key):
            subnets_already_tagged.append(subnet_id)
        else:
            subnets_to_tag.append((subnet_id, az, subnet_type))
    
    display_subnet_tags_table(subnet_data_with_tags)
    
    print(f"{Fore.CYAN}Tag Analysis:{Style.RESET_ALL}")
    print(f"  {Fore.GREEN}Already have correct tag: {len(subnets_already_tagged)}{Style.RESET_ALL}")
    print(f"  {Fore.YELLOW}Need tagging: {len(subnets_to_tag)}{Style.RESET_ALL}")
    
    if subnets_to_tag:
        print(f"\n{Fore.YELLOW}The following subnets will be tagged:{Style.RESET_ALL}")
        for subnet_id, az, subnet_type in subnets_to_tag:
            print(f"  - {subnet_id} ({subnet_type}, {az})")
        
        print(f"\n{Fore.YELLOW}Tag to be added: {Fore.WHITE}{tag_key}={cluster_name}{Style.RESET_ALL}")
        
        if not auto_confirm:
            response = input(f"\n{Fore.YELLOW}Proceed with tagging these subnets? (yes/no): {Style.RESET_ALL}").strip().lower()
            
            if response not in ['yes', 'y']:
                print_warning("Subnet tagging cancelled by user")
                print_error("Cannot proceed without tagging subnets for Karpenter")
                return False
        else:
            print_info("Auto-confirm mode: Proceeding with subnet tagging")
        
        print_info("Tagging subnets now...")
        
        for subnet_id, az, subnet_type in subnets_to_tag:
            print_info(f"Tagging {subnet_type} subnet {subnet_id} ({az})...")
            
            if tag_subnet_for_karpenter(subnet_id, cluster_name, region, tag_key):
                print_success(f"  Successfully tagged: {subnet_id}")
            else:
                print_error(f"  Failed to tag: {subnet_id}")
                subnets_failed.append(subnet_id)
    
    print(f"\n{Fore.CYAN}Subnet Tagging Summary:{Style.RESET_ALL}")
    print(f"  {Fore.GREEN}Already tagged: {len(subnets_already_tagged)}{Style.RESET_ALL}")
    print(f"  {Fore.GREEN}Newly tagged: {len(subnets_to_tag) - len(subnets_failed)}{Style.RESET_ALL}")
    
    if subnets_failed:
        print(f"  {Fore.RED}Failed: {len(subnets_failed)}{Style.RESET_ALL}")
        print_error("Some subnets could not be tagged. This may cause issues with Karpenter.")
        
        if not auto_confirm:
            response = input(f"{Fore.YELLOW}Continue anyway? (yes/no): {Style.RESET_ALL}").strip().lower()
            if response not in ['yes', 'y']:
                print_warning("Cluster creation cancelled")
                return False
        else:
            print_warning("Auto-confirm mode: Continuing despite failed subnet tags")
    else:
        print_success("All subnets are properly tagged for Karpenter")
    
    return True

def validate_yaml_structure(data, required_keys, context):
    """Validate that required keys exist in YAML data."""
    for key_path in required_keys:
        keys = key_path.split('.')
        current = data
        for key in keys:
            if not isinstance(current, dict) or key not in current:
                print_error(f"Missing required key '{key_path}' in {context}")
                return False
            current = current[key]
    return True

def set_cluster_metadata(base_config, cluster_input):
    """Set cluster metadata from input configuration."""
    print_info("Setting cluster metadata...")
    
    try:
        cluster_name = cluster_input["eks-cluster"]["name"]
        cluster_region = cluster_input["eks-cluster"]["region"]
        
        if not cluster_name or not isinstance(cluster_name, str):
            raise ValueError("Cluster name must be a non-empty string")
        if not cluster_region or not isinstance(cluster_region, str):
            raise ValueError("Cluster region must be a non-empty string")
        
        base_config["metadata"]["name"] = cluster_name
        base_config["metadata"]["region"] = cluster_region
        
        if "tags" in base_config["metadata"]:
            if "karpenter.sh/discovery" in base_config["metadata"]["tags"]:
                base_config["metadata"]["tags"]["karpenter.sh/discovery"] = cluster_name
        
        print_success(f"Cluster name: {cluster_name}")
        print_success(f"Cluster region: {cluster_region}")
        
    except KeyError as e:
        print_error(f"Missing required configuration key: {e}")
        raise
    except ValueError as e:
        print_error(f"Invalid configuration value: {e}")
        raise


def set_vpc_config(base_config, cluster_input):
    """Set VPC configuration from input configuration."""
    print_info("Setting VPC configuration...")
    
    try:
        vpc_id = cluster_input["vpc"]["id"]
        if not vpc_id or not isinstance(vpc_id, str):
            raise ValueError("VPC ID must be a non-empty string")
        
        base_config["vpc"]["id"] = vpc_id
        print_success(f"VPC ID: {vpc_id}")
        
        if "subnets" not in base_config["vpc"]:
            raise ValueError("VPC subnets not found in the base config")
        
        if "subnets" not in cluster_input["vpc"]:
            raise ValueError("VPC subnets not found in the input config")
        
        if "private" in cluster_input["vpc"]["subnets"]:
            private_subnets = cluster_input["vpc"]["subnets"]["private"]
            if not private_subnets or not isinstance(private_subnets, dict):
                raise ValueError("Private subnets must be a non-empty dictionary")
            base_config["vpc"]["subnets"]["private"] = private_subnets
            print_success(f"Private subnets: {len(private_subnets)} configured")
        
        
        if "public" in cluster_input["vpc"]["subnets"]:
            public_subnets = cluster_input["vpc"]["subnets"]["public"]
            if not public_subnets or not isinstance(public_subnets, dict):
                raise ValueError("Public subnets must be a non-empty dictionary")
            base_config["vpc"]["subnets"]["public"] = public_subnets
            print_success(f"Public subnets: {len(public_subnets)} configured")
        
        if "private" not in cluster_input["vpc"]["subnets"] or not cluster_input["vpc"]["subnets"]["private"]:
            print_warning(
                "No private subnets provided. Switching Managed Nodes to public networking.")
            for ng in base_config.get("managedNodeGroups", []):
                ng["privateNetworking"] = False
            
            base_config["vpc"]["subnets"].pop("private")

        if "public" not in cluster_input["vpc"]["subnets"] or not cluster_input["vpc"]["subnets"]["public"]:
            base_config["vpc"]["subnets"].pop("public")


    except KeyError as e:
        print_error(f"Missing required VPC configuration key: {e}")
        raise
    except ValueError as e:
        print_error(f"Invalid VPC configuration value: {e}")
        raise

def get_user_input():
    """Get and validate user input for cluster configuration file."""
    print_info("Please provide the path to your Cortex values file")
    print_info("(Press Ctrl+C to cancel)")
    
    while True:
        try:
            cluster_input_path = input(f"{Fore.YELLOW}Enter path: {Style.RESET_ALL}").strip()
            
            if not cluster_input_path:
                print_warning("Path cannot be empty. Please try again.")
                continue
            
            path = Path(cluster_input_path).expanduser().resolve()
            
            if not path.exists():
                print_error(f"File does not exist: {path}")
                retry = input(f"{Fore.YELLOW}Try again? (y/n): {Style.RESET_ALL}").strip().lower()
                if retry != 'y':
                    return None
                continue
            
            if not path.is_file():
                print_error(f"Path is not a file: {path}")
                continue
            
            print_success(f"Using configuration file: {path}")
            return str(path)
            
        except KeyboardInterrupt:
            print(f"\n{Fore.YELLOW}Operation cancelled by user{Style.RESET_ALL}")
            return None
        except Exception as e:
            print_error(f"Error processing path: {e}")
            return None

def confirm_cluster_creation(cluster_name, region):
    """Ask user to confirm cluster creation."""
    print(f"\n{Fore.MAGENTA}{Style.BRIGHT}Cluster Configuration Summary:{Style.RESET_ALL}")
    print(f"  Cluster Name: {Fore.WHITE}{Style.BRIGHT}{cluster_name}{Style.RESET_ALL}")
    print(f"  Region: {Fore.WHITE}{Style.BRIGHT}{region}{Style.RESET_ALL}")
    print(f"  Output File: {Fore.WHITE}{Style.BRIGHT}{OUTPUT_PATH}{Style.RESET_ALL}")
    print(f"\n{Fore.YELLOW}⚠ This will create a new EKS cluster which may incur AWS costs.{Style.RESET_ALL}")
    
    while True:
        response = input(f"{Fore.YELLOW}Do you want to proceed? (yes/no): {Style.RESET_ALL}").strip().lower()
        if response in ['yes', 'y']:
            return True
        elif response in ['no', 'n']:
            print_warning("Cluster creation cancelled by user")
            return False
        else:
            print_warning("Please enter 'yes' or 'no'")

def parse_arguments():
    parser = argparse.ArgumentParser(description='EKS Cluster Setup Script')
    parser.add_argument('--values', '-f', required=True, help='Path to values YAML file')
    parser.add_argument('-y', '--yes', action='store_true', help='Skip all confirmation prompts')
    return parser.parse_args()

def main():
    try:
        args = parse_arguments()
        
        print_header("EKS Cluster Setup Script")
        print_info(f"Log file: {LOG_FILE}")
        
        if not check_prerequisites():
            print_error("Prerequisites check failed. Exiting.")
            sys.exit(1)
        
        if not check_aws_credentials():
            print_error("AWS credentials check failed. Exiting.")
            sys.exit(1)
        
        print_header("Configuration Files")
        
        if not check_file_exists(BASE_CONFIG_PATH):
            print_error(f"Base configuration file not found: {BASE_CONFIG_PATH}")
            sys.exit(1)
        
        cluster_input_path = Path(args.values)
        if not cluster_input_path.exists():
            print_error(f"Values file not found: {cluster_input_path}")
            sys.exit(1)
        print_success(f"Using values file: {cluster_input_path}")
        
        print_header("Loading Configuration")
        
        try:
            with open(BASE_CONFIG_PATH, 'r') as f:
                base_config = yaml.safe_load(f)
            print_success("Loaded base configuration")
        except yaml.YAMLError as e:
            print_error(f"Failed to parse base config YAML: {e}")
            sys.exit(1)
        except Exception as e:
            print_error(f"Failed to read base config: {e}")
            sys.exit(1)
        
        try:
            with open(cluster_input_path, 'r') as f:
                cluster_input = yaml.safe_load(f)
            print_success("Loaded cluster input configuration")
        except yaml.YAMLError as e:
            print_error(f"Failed to parse input config YAML: {e}")
            sys.exit(1)
        except Exception as e:
            print_error(f"Failed to read input config: {e}")
            sys.exit(1)
        
        required_input_keys = [
            'eks-cluster.name',
            'eks-cluster.region',
            'vpc.id',
            'vpc.subnets'
        ]
        
        if not validate_yaml_structure(cluster_input, required_input_keys, "input configuration"):
            print_error("Input configuration validation failed. Exiting.")
            sys.exit(1)
        
        print_header("Configuring Cluster")
        
        set_cluster_metadata(base_config, cluster_input)
        set_vpc_config(base_config, cluster_input)
        
        print_header("Writing Configuration")
        
        try:
            with open(OUTPUT_PATH, 'w') as f:
                yaml.dump(base_config, f, default_flow_style=False, sort_keys=False)
            print_success(f"Cluster configuration written to: {OUTPUT_PATH}")
        except Exception as e:
            print_error(f"Failed to write output configuration: {e}")
            sys.exit(1)
        
        cluster_name = base_config["metadata"]["name"]
        region = base_config["metadata"]["region"]
        
        karpenter_config = base_config.get('karpenter', {})
        karpenter_version = karpenter_config.get('version', DEFAULT_KARPENTER_VERSION)
        karpenter_namespace = karpenter_config.get('namespace', 'karpenter')
        
        print_info(f"Karpenter Version: {karpenter_version}")
        
        aws_account_id = get_aws_account_id()
        if not aws_account_id:
            print_error("Could not get AWS account ID")
            sys.exit(1)
        print_success(f"AWS Account ID: {aws_account_id}")
        
        if 'iam' in base_config:
            if 'podIdentityAssociations' in base_config['iam']:
                for assoc in base_config['iam']['podIdentityAssociations']:
                    if 'roleName' in assoc:
                        assoc['roleName'] = assoc['roleName'].replace('${CLUSTER_NAME}', cluster_name)
                    if 'permissionPolicyARNs' in assoc:
                        assoc['permissionPolicyARNs'] = [
                            arn.replace('${AWS_ACCOUNT_ID}', aws_account_id).replace('${CLUSTER_NAME}', cluster_name)
                            for arn in assoc['permissionPolicyARNs']
                        ]
        
        if 'iamIdentityMappings' in base_config:
            for mapping in base_config['iamIdentityMappings']:
                if 'arn' in mapping:
                    mapping['arn'] = mapping['arn'].replace('${AWS_ACCOUNT_ID}', aws_account_id).replace('${CLUSTER_NAME}', cluster_name)
        
        if 'karpenter' in base_config:
            del base_config['karpenter']
        
        try:
            with open(OUTPUT_PATH, 'w') as f:
                yaml.dump(base_config, f, default_flow_style=False, sort_keys=False)
            print_success("Updated cluster configuration with IAM settings")
        except Exception as e:
            print_error(f"Failed to update configuration: {e}")
            sys.exit(1)
        
        if not ensure_subnets_tagged(cluster_input, cluster_name, region, args.yes):
            print_error("Subnet tagging failed or was cancelled. Exiting.")
            sys.exit(1)
        
        if not deploy_karpenter_cloudformation(cluster_name, karpenter_version, region):
            print_error("Karpenter CloudFormation deployment failed. Exiting.")
            sys.exit(1)
        
        if not args.yes and not confirm_cluster_creation(cluster_name, region):
            print_info("Exiting without creating cluster.")
            sys.exit(0)
        
        if args.yes:
            print_info("Auto-confirm mode: Proceeding with cluster creation")
        
        print_header("Creating EKS Cluster")
        print_info("This may take 15-20 minutes...")
        print_info(f"Running: eksctl create cluster -f {OUTPUT_PATH}")
        
        try:
            subprocess.run(
                ['eksctl', 'create', 'cluster', '-f', str(OUTPUT_PATH)],
                check=True,
                text=True
            )
            
            print_header("Cluster Creation Complete")
            print_success(f"EKS cluster '{cluster_name}' created successfully!")
            print_info(f"Region: {region}")
            print_info("Next steps:")
            print(f"  1. Verify cluster: {Fore.WHITE}kubectl get nodes{Style.RESET_ALL}")
            print("  2. Install Karpenter (next step in your setup)")
            print_info(f"Log file saved to: {LOG_FILE}")
            
        except subprocess.CalledProcessError as e:
            print_error(f"Failed to create EKS cluster: {e}")
            print_error("Check the logs above for details")
            sys.exit(1)
        except KeyboardInterrupt:
            print_warning("\nCluster creation interrupted by user")
            print_warning("The cluster may be partially created. Check AWS console.")
            sys.exit(1)
        
    except KeyboardInterrupt:
        print(f"\n{Fore.YELLOW}Operation cancelled by user{Style.RESET_ALL}")
        sys.exit(1)
    except Exception as e:
        print_error(f"Unexpected error: {e}")
        logger.exception("Unexpected error occurred")
        sys.exit(1)

if __name__ == "__main__":
    main()

    