#!/usr/bin/env python3

import sys
import yaml
import subprocess
import logging
import argparse
import json
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
KARPENTER_DIR = SCRIPT_DIR.parent / "karpenter"
CLUSTER_CONFIG_PATH = SCRIPT_DIR / "cluster" / "cortex.eks.yaml"
LOG_FILE = SCRIPT_DIR / f"karpenter-setup-{datetime.now().strftime('%Y%m%d-%H%M%S')}.log"

KARPENTER_NAMESPACE = "karpenter"
KARPENTER_VERSION = "1.0.6"
KARPENTER_CHART_VERSION = "1.0.6"

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
    print(f"\n{Fore.CYAN}{Style.BRIGHT}{'='*60}")
    print(f"{Fore.CYAN}{Style.BRIGHT}{message.center(60)}")
    print(f"{Fore.CYAN}{Style.BRIGHT}{'='*60}{Style.RESET_ALL}\n")

def print_success(message):
    print(f"{Fore.GREEN}✓ {message}{Style.RESET_ALL}")
    logger.info(message)

def print_error(message):
    print(f"{Fore.RED}✗ {message}{Style.RESET_ALL}")
    logger.error(message)

def print_warning(message):
    print(f"{Fore.YELLOW}⚠ {message}{Style.RESET_ALL}")
    logger.warning(message)

def print_info(message):
    print(f"{Fore.BLUE}ℹ {message}{Style.RESET_ALL}")
    logger.info(message)

def check_prerequisites():
    print_info("Checking prerequisites...")
    
    required_tools = {
        'kubectl': 'kubectl version --client',
        'helm': 'helm version',
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

def check_cluster_connection():
    print_info("Checking cluster connection...")
    try:
        result = subprocess.run(
            ['kubectl', 'cluster-info'],
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode == 0:
            print_success("Connected to Kubernetes cluster")
            return True
        else:
            print_error("Cannot connect to Kubernetes cluster")
            print_info("Make sure your kubeconfig is set up correctly")
            return False
    except Exception as e:
        print_error(f"Failed to check cluster connection: {e}")
        return False

def load_cluster_config():
    print_info("Loading cluster configuration...")
    
    if not CLUSTER_CONFIG_PATH.exists():
        print_error(f"Cluster config not found: {CLUSTER_CONFIG_PATH}")
        print_info("Please run the EKS cluster setup script first")
        return None
    
    try:
        with open(CLUSTER_CONFIG_PATH, 'r') as f:
            config = yaml.safe_load(f)
        print_success("Loaded cluster configuration")
        return config
    except Exception as e:
        print_error(f"Failed to load cluster config: {e}")
        return None

def get_cluster_metadata(cluster_config):
    if not cluster_config or 'metadata' not in cluster_config:
        return None, None, None, None, None
    
    metadata = cluster_config['metadata']
    cluster_name = metadata.get('name')
    region = metadata.get('region')
    k8s_version = metadata.get('version', '1.30')
    
    karpenter_config = cluster_config.get('karpenter', {})
    tag_key = karpenter_config.get('tag-key', 'cortex/karpenter-discovery')
    karpenter_version = karpenter_config.get('version', KARPENTER_VERSION)
    
    return cluster_name, region, k8s_version, tag_key, karpenter_version

def get_availability_zones(cluster_config):
    zones = set()
    
    if 'vpc' in cluster_config and 'subnets' in cluster_config['vpc']:
        subnets = cluster_config['vpc']['subnets']
        
        for subnet_type in ['private', 'public']:
            if subnet_type in subnets:
                for az in subnets[subnet_type].keys():
                    zones.add(az)
    
    return sorted(list(zones))

def load_karpenter_files():
    print_info("Loading Karpenter configuration files...")
    
    files = {
        'node_class': KARPENTER_DIR / 'node-class.yaml',
        'node_pool': KARPENTER_DIR / 'node-pool.yaml',
        'clickhouse_pool': KARPENTER_DIR / 'clickhouse-node-pool.yaml'
    }
    
    configs = {}
    
    for name, path in files.items():
        if not path.exists():
            print_warning(f"File not found: {path}")
            continue
        
        try:
            with open(path, 'r') as f:
                content = f.read()
                docs = list(yaml.safe_load_all(content))
                configs[name] = {'path': path, 'content': content, 'docs': docs}
                print_success(f"Loaded {path.name}")
        except Exception as e:
            print_error(f"Failed to load {path.name}: {e}")
    
    return configs

def update_node_class(node_class_doc, cluster_name, tag_key):
    if not node_class_doc:
        return node_class_doc
    
    if 'spec' not in node_class_doc:
        node_class_doc['spec'] = {}
    
    spec = node_class_doc['spec']
    
    spec['role'] = f"KarpenterNodeRole-{cluster_name}"
    
    if 'subnetSelectorTerms' not in spec:
        spec['subnetSelectorTerms'] = []
    
    if spec['subnetSelectorTerms']:
        spec['subnetSelectorTerms'][0]['tags'] = {tag_key: cluster_name}
    else:
        spec['subnetSelectorTerms'] = [{'tags': {tag_key: cluster_name}}]
    
    if 'securityGroupSelectorTerms' not in spec:
        spec['securityGroupSelectorTerms'] = []
    
    if spec['securityGroupSelectorTerms']:
        spec['securityGroupSelectorTerms'][0]['tags'] = {tag_key: cluster_name}
    else:
        spec['securityGroupSelectorTerms'] = [{'tags': {tag_key: cluster_name}}]
    
    return node_class_doc

def update_node_pool_zones(node_pool_doc, zones):
    if not node_pool_doc or not zones:
        return node_pool_doc
    
    if 'spec' in node_pool_doc and 'template' in node_pool_doc['spec']:
        template = node_pool_doc['spec']['template']
        if 'spec' in template and 'requirements' in template['spec']:
            requirements = template['spec']['requirements']
            
            for req in requirements:
                if req.get('key') == 'topology.kubernetes.io/zone':
                    req['values'] = zones
                    break
    
    return node_pool_doc

def get_cluster_security_groups(cluster_name, region):
    try:
        result = subprocess.run(
            [
                'aws', 'eks', 'describe-cluster',
                '--name', cluster_name,
                '--region', region,
                '--output', 'json'
            ],
            capture_output=True,
            text=True,
            timeout=30
        )
        if result.returncode != 0:
            print_error(f"Failed to describe cluster for security groups: {result.stderr}")
            return []

        data = json.loads(result.stdout)
        vpc_cfg = data.get('cluster', {}).get('resourcesVpcConfig', {})
        sg_ids = set()

        cluster_sg = vpc_cfg.get('clusterSecurityGroupId')
        if cluster_sg:
            sg_ids.add(cluster_sg)

        for sg in vpc_cfg.get('securityGroupIds', []) or []:
            sg_ids.add(sg)

        return sorted(list(sg_ids))
    except Exception as e:
        print_error(f"Failed to get cluster security groups: {e}")
        return []

def get_resource_tags(resource_id, region):
    try:
        result = subprocess.run(
            [
                'aws', 'ec2', 'describe-tags',
                '--filters', f'Name=resource-id,Values={resource_id}',
                '--region', region,
                '--output', 'json'
            ],
            capture_output=True,
            text=True,
            timeout=20
        )
        if result.returncode != 0:
            print_error(f"Failed to get tags for {resource_id}: {result.stderr}")
            return {}

        data = json.loads(result.stdout)
        return {t['Key']: t['Value'] for t in data.get('Tags', [])}
    except Exception as e:
        print_error(f"Failed to get tags for {resource_id}: {e}")
        return {}

def tag_resource(resource_id, region, tag_key, tag_value):
    try:
        result = subprocess.run(
            [
                'aws', 'ec2', 'create-tags',
                '--resources', resource_id,
                '--tags', f'Key={tag_key},Value={tag_value}',
                '--region', region
            ],
            capture_output=True,
            text=True,
            timeout=20
        )
        if result.returncode != 0:
            print_error(f"Failed to tag {resource_id} with {tag_key}={tag_value}: {result.stderr}")
            return False
        return True
    except Exception as e:
        print_error(f"Failed to tag {resource_id}: {e}")
        return False

def ensure_security_groups_tagged(cluster_name, region, tag_key, auto_confirm=False):
    print_header("Karpenter Security Group Tagging")
    print_info(f"Security groups will be tagged with: {Fore.WHITE}{Style.BRIGHT}{tag_key}={cluster_name}{Style.RESET_ALL}")

    sg_ids = get_cluster_security_groups(cluster_name, region)
    if not sg_ids:
        print_error("No security groups discovered from EKS cluster. Cannot proceed.")
        return False

    to_tag = []
    for sg_id in sg_ids:
        tags = get_resource_tags(sg_id, region)
        if tags.get(tag_key) != cluster_name:
            to_tag.append(sg_id)

    if not to_tag:
        print_success("All cluster security groups already have the required Karpenter discovery tag")
        return True

    print_warning(f"{len(to_tag)} security group(s) are missing tag {tag_key}={cluster_name}:")
    for sg_id in to_tag:
        print(f"  - {sg_id}")

    if not auto_confirm:
        response = input(f"\n{Fore.YELLOW}Proceed with tagging these security groups? (yes/no): {Style.RESET_ALL}").strip().lower()
        if response not in ['yes', 'y']:
            print_warning("Security group tagging cancelled by user")
            return False
    else:
        print_info("Auto-confirm mode: Proceeding with security group tagging")

    failed = []
    for sg_id in to_tag:
        print_info(f"Tagging security group {sg_id}...")
        if tag_resource(sg_id, region, tag_key, cluster_name):
            print_success(f"  Successfully tagged: {sg_id}")
        else:
            failed.append(sg_id)

    if failed:
        print_error(f"Failed to tag {len(failed)} security group(s).")
        return False

    print_success("All required security groups are tagged for Karpenter")
    return True

def save_updated_config(config_name, docs, output_dir):
    output_path = output_dir / f"{config_name}.yaml"
    
    try:
        with open(output_path, 'w') as f:
            for i, doc in enumerate(docs):
                if i > 0:
                    f.write('\n---\n')
                yaml.dump(doc, f, default_flow_style=False, sort_keys=False)
        
        print_success(f"Saved updated config: {output_path.name}")
        return output_path
    except Exception as e:
        print_error(f"Failed to save {output_path.name}: {e}")
        return None

def install_karpenter_helm(cluster_name, region, version):
    print_header("Installing Karpenter via Helm")
    
    print_info("Getting cluster endpoint...")
    try:
        endpoint_result = subprocess.run(
            ['aws', 'eks', 'describe-cluster', '--name', cluster_name, '--region', region, '--query', 'cluster.endpoint', '--output', 'text'],
            capture_output=True,
            text=True,
            timeout=30
        )
        if endpoint_result.returncode != 0:
            print_error(f"Failed to get cluster endpoint: {endpoint_result.stderr}")
            return False
        cluster_endpoint = endpoint_result.stdout.strip()
        print_success(f"Cluster endpoint: {cluster_endpoint[:50]}...")
    except Exception as e:
        print_error(f"Failed to get cluster endpoint: {e}")
        return False
    
    print_info("Getting Karpenter IAM role ARN...")
    try:
        role_result = subprocess.run(
            ['aws', 'iam', 'get-role', '--role-name', f'{cluster_name}-karpenter', '--query', 'Role.Arn', '--output', 'text'],
            capture_output=True,
            text=True,
            timeout=30
        )
        if role_result.returncode != 0:
            print_error(f"Failed to get Karpenter IAM role: {role_result.stderr}")
            print_info("Make sure the CloudFormation stack was deployed during EKS setup")
            return False
        karpenter_role_arn = role_result.stdout.strip()
        print_success(f"Karpenter role ARN: {karpenter_role_arn}")
    except Exception as e:
        print_error(f"Failed to get Karpenter IAM role: {e}")
        return False
    
    print_info(f"Installing Karpenter {version}...")
    print_info("This may take a few minutes...")
    
    try:
        cmd = [
            'helm', 'upgrade', '--install', 'karpenter',
            'oci://public.ecr.aws/karpenter/karpenter',
            '--version', version,
            '--namespace', KARPENTER_NAMESPACE,
            '--create-namespace',
            '--set', f'settings.clusterName={cluster_name}',
            '--set', f'settings.clusterEndpoint={cluster_endpoint}',
            '--set', f'serviceAccount.annotations.eks\\.amazonaws\\.com/role-arn={karpenter_role_arn}',
            '--wait'
        ]
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300
        )
        
        if result.returncode == 0:
            print_success("Karpenter installed successfully")
            return True
        else:
            print_error(f"Karpenter installation failed: {result.stderr}")
            return False
            
    except subprocess.TimeoutExpired:
        print_error("Karpenter installation timed out")
        return False
    except Exception as e:
        print_error(f"Failed to install Karpenter: {e}")
        return False

def apply_karpenter_configs(config_paths):
    print_header("Applying Karpenter Configurations")
    
    for config_path in config_paths:
        if not config_path:
            continue
        
        print_info(f"Applying {config_path.name}...")
        try:
            result = subprocess.run(
                ['kubectl', 'apply', '-f', str(config_path)],
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode == 0:
                print_success(f"Applied {config_path.name}")
            else:
                print_error(f"Failed to apply {config_path.name}: {result.stderr}")
                return False
        except Exception as e:
            print_error(f"Error applying {config_path.name}: {e}")
            return False
    
    return True

def parse_arguments():
    parser = argparse.ArgumentParser(description='Karpenter Setup Script')
    parser.add_argument('-y', '--yes', action='store_true', help='Skip all confirmation prompts')
    return parser.parse_args()

def main():
    try:
        args = parse_arguments()
        auto_confirm = args.yes
        
        print_header("Karpenter Setup Script")
        print_info(f"Log file: {LOG_FILE}")
        
        if auto_confirm:
            print_info("Auto-confirm mode enabled (-y)")
        
        if not check_prerequisites():
            print_error("Prerequisites check failed. Exiting.")
            sys.exit(1)
        
        if not check_cluster_connection():
            print_error("Cluster connection check failed. Exiting.")
            sys.exit(1)
        
        print_header("Loading Configuration")
        
        cluster_config = load_cluster_config()
        if not cluster_config:
            sys.exit(1)
        
        cluster_name, region, k8s_version, tag_key, karpenter_version = get_cluster_metadata(cluster_config)
        
        if not cluster_name or not region:
            print_error("Could not extract cluster metadata")
            sys.exit(1)
        
        print_success(f"Cluster: {cluster_name}")
        print_success(f"Region: {region}")
        print_success(f"K8s Version: {k8s_version}")
        print_success(f"Tag Key: {tag_key}")
        print_success(f"Karpenter Version: {karpenter_version}")
        
        zones = get_availability_zones(cluster_config)
        if zones:
            print_success(f"Availability Zones: {', '.join(zones)}")
        
        karpenter_configs = load_karpenter_files()
        if not karpenter_configs:
            print_error("No Karpenter configuration files found")
            sys.exit(1)
        
        print_header("Updating Configurations")
        
        output_dir = SCRIPT_DIR / "generated"
        output_dir.mkdir(exist_ok=True)
        
        updated_paths = []
        
        if 'node_class' in karpenter_configs:
            docs = karpenter_configs['node_class']['docs']
            for doc in docs:
                if doc and doc.get('kind') == 'EC2NodeClass':
                    update_node_class(doc, cluster_name, tag_key)
            
            path = save_updated_config('node-class', docs, output_dir)
            if path:
                updated_paths.append(path)
        
        for config_name in ['node_pool', 'clickhouse_pool']:
            if config_name in karpenter_configs:
                docs = karpenter_configs[config_name]['docs']
                for doc in docs:
                    if doc and doc.get('kind') == 'NodePool':
                        update_node_pool_zones(doc, zones)
                
                path = save_updated_config(config_name.replace('_', '-'), docs, output_dir)
                if path:
                    updated_paths.append(path)
        
        print(f"\n{Fore.MAGENTA}{Style.BRIGHT}Configuration Summary:{Style.RESET_ALL}")
        print(f"  Cluster: {Fore.WHITE}{Style.BRIGHT}{cluster_name}{Style.RESET_ALL}")
        print(f"  Karpenter Version: {Fore.WHITE}{Style.BRIGHT}{karpenter_version}{Style.RESET_ALL}")
        print(f"  Configurations: {Fore.WHITE}{Style.BRIGHT}{len(updated_paths)} files{Style.RESET_ALL}")
        
        if not auto_confirm:
            response = input(f"\n{Fore.YELLOW}Proceed with Karpenter installation? (yes/no): {Style.RESET_ALL}").strip().lower()
            
            if response not in ['yes', 'y']:
                print_warning("Installation cancelled by user")
                sys.exit(0)
        else:
            print_info("Auto-confirm mode: Proceeding with installation")

        if not ensure_security_groups_tagged(cluster_name, region, tag_key, auto_confirm):
            print_error("Security group tagging failed. Cannot proceed with Karpenter provisioning.")
            sys.exit(1)
        
        if not install_karpenter_helm(cluster_name, region, karpenter_version):
            print_error("Karpenter Helm installation failed")
            sys.exit(1)
        
        if not apply_karpenter_configs(updated_paths):
            print_error("Failed to apply Karpenter configurations")
            sys.exit(1)
        
        print_header("Installation Complete")
        print_success("Karpenter installed and configured successfully!")
        print_info("Next steps:")
        print(f"  1. Verify Karpenter pods: {Fore.WHITE}kubectl get pods -n {KARPENTER_NAMESPACE}{Style.RESET_ALL}")
        print(f"  2. Check node pools: {Fore.WHITE}kubectl get nodepools{Style.RESET_ALL}")
        print(f"  3. Check node classes: {Fore.WHITE}kubectl get ec2nodeclasses{Style.RESET_ALL}")
        print_info(f"Log file saved to: {LOG_FILE}")
        
    except KeyboardInterrupt:
        print(f"\n{Fore.YELLOW}Operation cancelled by user{Style.RESET_ALL}")
        sys.exit(1)
    except Exception as e:
        print_error(f"Unexpected error: {e}")
        logger.exception("Unexpected error occurred")
        sys.exit(1)

if __name__ == "__main__":
    main()
