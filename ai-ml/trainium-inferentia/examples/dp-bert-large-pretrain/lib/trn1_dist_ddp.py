import torchx.specs as specs
import shlex
import os
import re

CACHE_ROOT = "/data/"

# Create an AppDef based on code snippets borrowed from the TorchX DDP builtin
#   see: https://github.com/pytorch/torchx/blob/main/torchx/components/dist.py
def generateAppDef(script_args: str, nnodes: int, nproc_per_node: int,
                   script: str, image: str, name: str, precompile: bool=False,
                   bf16: bool=False, cacheset: str="default",
                   instance_type: str="trn1.32xlarge") -> specs.AppDef:

    # Define the location of the Neuron compiler cache and Transformers cache on shared storage.
    # Note: "cacheset" is a unique name/key chosen by the user to distinguish between jobs. The
    # user must ensure that only one job is running for a given cacheset at any given time in
    # order to avoid corrupting the cache
    if not re.match(r"^[A-Za-z0-9_-]+$", cacheset):
        raise ValueError("Error: please make sure that 'cacheset' contains only letters, numbers, dashes, and underscores.")

    NEURON_CACHE = os.path.join(CACHE_ROOT, "neuron_cache", f"{cacheset}_{specs.macros.replica_id}")
    TRANSFORMERS_CACHE = os.path.join(CACHE_ROOT, "transformers_cache", f"{cacheset}_{specs.macros.replica_id}")

    cmd = [
        "python3",
        "-m",
        "torch.distributed.run",
        "--rdzv_backend",
        "etcd",
        "--rdzv_endpoint",
        "etcd-server:2379",
        "--rdzv_id",
        f"{specs.macros.app_id}",
        "--nnodes",
        str(nnodes),
        "--nproc_per_node",
        str(nproc_per_node),
        "--tee",
        "3",
        "--role",
        "",
        script
    ]

    env_mapping = {
            "CCOM_SOCKET_IFNAME": "eth0",
            "FI_EFA_USE_DEVICE_RDMA": "1",
            "FI_PROVIDER": "efa",
            "FI_EFA_FORK_SAFE": "1",
            "NEURON_RT_RESET_CORES": "1",
            "XLA_TRANSFER_SEED_ASYNC": "1",
            "NEURON_CC_FLAGS": f"--cache_dir={NEURON_CACHE}",
            "TRANSFORMERS_CACHE": TRANSFORMERS_CACHE
            }

    # Configure BF16 if requested by user
    if bf16:
        env_mapping["XLA_DOWNCAST_BF16"] = "1"

    instance_type = instance_type.lower()
    if instance_type == "trn1n.32xlarge":
        num_efas = 16
    elif instance_type == "trn1.32xlarge":
        num_efas = 8
    else:
        raise Exception(f"Instance type {instance_type} is not supported.\n"
                        + "Please try trn1.32xlarge or trn1n.32xlarge.")

    resourcedef = specs.Resource(cpu=0, gpu=0, memMB=0,
            capabilities={"node.kubernetes.io/instance-type": instance_type},
            devices={"aws.amazon.com/neuron": 16, "vpc.amazonaws.com/efa": num_efas})

    print(f"resourcedef: {resourcedef}")

    # Adjust entrypoint and args depending on whether neuron_parallel_compile
    # is requested
    if precompile:
        entrypoint = "neuron_parallel_compile"
        args = [_args_join(cmd) + " " + script_args]
    else:
        entrypoint = "bash"
        args = ["-c", _args_join(cmd) + " " + script_args]

    appdef = specs.AppDef(
        name=name or "test_name",
        roles=[
            specs.Role(
                name="role1",
                image=image,
                entrypoint=entrypoint,
                num_replicas=nnodes,
                resource=resourcedef,
                args=args,
                env=env_mapping,
                max_retries=3,
                retry_policy=specs.RetryPolicy("APPLICATION"),
                mounts=[specs.VolumeMount(src="fsx-claim", dst_path="/data")]
            )
        ],
    )

    print(f"appdef: {appdef}")
    return appdef


def _args_join(args):
    """
    _args_join is like shlex.join but if the argument is wrapped in _noquote
    it'll not quote that argument.
    """
    quoted = [arg if isinstance(arg, _noquote) else shlex.quote(arg) for arg in args]
    return " ".join(quoted)

class _noquote(str):
    """
    _noquote is a wrapper around str that indicates that the argument shouldn't
    be passed through shlex.quote.
    """

    pass
