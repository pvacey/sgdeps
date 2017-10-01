#!/usr/bin/env python
from __future__ import print_function
from boto.ec2 import regions
import boto.ec2, boto.ec2.elb, boto.rds2, boto.redshift, boto.elasticache
from sys import exit
import argparse
import textwrap
from Queue import Queue
from threading import Thread

class Sg_obj(object):

    """class to hold object which will use security group"""

    def __init__(self, sgid, service, id,  name):
        self.sgid = sgid
        self.service= service
        self.id = id
        self.name= name

    def __repr__(self):
        if self.name:
            return self.service+": "+ self.id + " (" + self.name +")"
        else:
            return self.service+": "+ self.id

class Sg_deps(object):

    """to list AWS security group dependencies"""

    def __init__(self, region_name, profile_name):
        """collect info for a region """
        if not region_name or region_name not in map(lambda x: x.name, regions()):
            print("\nError: please specify a valid region name with --region ")
            print("  valid regions: " + ", ".join(map(lambda x: x.name, regions()))+ "\n")
            exit(1)
        self.region = region_name
        self.profile = profile_name
        self.sg_by_id={}
        self.sg_by_name={}
        self.queue = Queue()

        self.service_list = ["ec2", "elb", "rds", "redshift", "elasticache", "eni"]

        try:
            self.sgs =  boto.ec2.connect_to_region(self.region, profile_name=self.profile).get_all_security_groups()
        except Exception as e:
            print("\nError: please check your credentials and network connectivity\n")
            exit(1)
        threads = []
        threads.append(Thread(target=self.prepare_sg))
        for service in self.service_list:
            threads.append(Thread(target=self.wrap, args=(service,)))
        [x.start() for x in threads]
        [x.join() for x in threads]
        while not self.queue.empty():
            obj = self.queue.get()
            self.sg_by_id[obj.sgid]["obj"].add(obj)

    def wrap(self, service):
        try:
            getattr(self, "list_"+service+"_sg")()
        except:
            pass

    def prepare_sg(self):
        for sg in self.sgs:
            self.sg_by_name[sg.name] = sg.id
            if sg.id not in self.sg_by_id:
                self.sg_by_id[sg.id] = {}
                self.sg_by_id[sg.id]["deps"]=set()
                self.sg_by_id[sg.id]["obj"]=set()
            self.sg_by_id[sg.id]["name"] = sg.name
            for rule in sg.rules:
                for grant in rule.grants:
                    if not grant.group_id:
                        continue
                    if grant.group_id not in self.sg_by_id:
                        self.sg_by_id[grant.group_id]={}
                        self.sg_by_id[grant.group_id]["deps"]=set()
                        self.sg_by_id[grant.group_id]["obj"]=set()
                    self.sg_by_id[grant.group_id]["deps"].add(sg.id)

    def list_eni_sg(self):
        instances = boto.ec2.connect_to_region(self.region, profile_name=self.profile).get_all_network_interfaces()
        for instance in instances:
            name = ""
            if "Name" in instance.tags:
                name = instance.tags["Name"]
            for group in instance.groups:
                self.queue.put(Sg_obj(group.id, "eni", instance.id, name))

    def list_ec2_sg(self):
        instances = reduce(lambda x,y: x+y, map(lambda x: x.instances, boto.ec2.connect_to_region(self.region, profile_name=self.profile).get_all_instances()))
        for instance in instances:
            for group in instance.groups:
                name = ""
                if "Name" in instance.tags:
                    name = instance.tags["Name"]
                self.queue.put(Sg_obj(group.id, "ec2", instance.id, name))

    def list_elb_sg(self):
        for elb in boto.ec2.elb.connect_to_region(self.region, profile_name=self.profile).get_all_load_balancers():
            for group in elb.security_groups:
                self.queue.put(Sg_obj(group, "elb", elb.name, ""))

    def list_rds_sg(self):
        for instance in  boto.rds2.connect_to_region(self.region, profile_name=self.profile).describe_db_instances()["DescribeDBInstancesResponse"]["DescribeDBInstancesResult"]["DBInstances"]:
            for group in instance["VpcSecurityGroups"]:
                self.queue.put(Sg_obj(group["VpcSecurityGroupId"], "rds", instance["DBInstanceIdentifier"], ""))

    def list_redshift_sg(self):
        for instance in boto.redshift.connect_to_region(self.region, profile_name=self.profile).describe_clusters()["DescribeClustersResponse"]["DescribeClustersResult"]["Clusters"]:
            for group in instance["VpcSecurityGroups"]:
                self.queue.put(Sg_obj(group["VpcSecurityGroupId"], "redshift",  instance["ClusterIdentifier"], ""))

    def list_elasticache_sg(self):
        for instance in boto.elasticache.connect_to_region(self.region, profile_name=self.profile).describe_cache_clusters()["DescribeCacheClustersResponse"]["DescribeCacheClustersResult"]["CacheClusters"]:
            for group in instance["SecurityGroups"]:
                self.queue.put(Sg_obj(group["SecurityGroupId"], "elasticache", instance["CacheClusterId"], ""))


    def show_obj(self, sgid):
        if not self.sg_by_id[sgid]["obj"]:
            print("\nNot used by any "+ "/".join(self.service_list)+ " instance")
        else:
            print("\nUsed by:")
            for obj in sorted(self.sg_by_id[sgid]["obj"], key=lambda x: x.service + x.name.lower() + x.id):
                print("  " + str(obj))

    def show_eni_only_sg(self, showlist=False):
        todo = []
        for sgid in self.sg_by_id:
            if self.sg_by_id[sgid]["obj"] and not filter(lambda x: x.service != "eni", self.sg_by_id[sgid]["obj"]):
                todo.append(sgid)
        if todo:
            print("\nBelow security group(s) are used by eni but not any of "+ "/".join(filter(lambda x: x!="eni", self.service_list))+" service\n")
            if showlist:
                print("\n".join([self._string_sg(x) for x in todo]))
            else:
                for sgid in todo:
                    self.show_sg(sgid)
        else:
            print("\nNot found")

    def show_obsolete_sg(self, showlist=False):
        todo = []
        for sgid in self.sg_by_id:
            if not self.sg_by_id[sgid]["obj"]:
                todo.append(sgid)
        if todo:
            print("\nBelow security group(s) are not used by any "+ "/".join(self.service_list)+" service\n")
            if showlist:
                print("\n".join([self._string_sg(x) for x in todo]))
            else:
                for sgid in todo:
                    self.show_sg(sgid)
        else:
            print("\nNot found")

    def show_sg(self, sg, showlist=False):
        if sg:
            if sg in self.sg_by_id:
                sgid = sg
            elif sg in self.sg_by_name:
                sgid= self.sg_by_name[sg]
            else:
                print("\nError: cannot find the security group with name or id: " + sg + "\n")
                exit(1)
            if showlist:
                print(self._string_sg(sgid))
            else:
                print("\n" + "-"*70)
                self._show(sgid, [], [])
                self.show_obj(sgid)
        else:
            for sgid in self.sg_by_id:
                    self.show_sg(sgid, showlist=showlist)

    def _show(self, sgid, previous, indent):
        if not previous:
            print(self._string_sg(sgid), end="")
        else:
            pre = "".join(["|  " if x else "   " for x in indent[:-1]])
            if indent[-1]:
                pre += "|--"
            else:
                pre += "`--"
            print(pre + " " + self._string_sg(sgid), end="")
        if sgid in previous:
            print(" ** loop")
            return
        else:
            print()
        deps =list(self.sg_by_id[sgid]["deps"])
        for dep in deps:
            if dep == deps[-1]:
                self._show(dep, previous+[sgid], indent+[False])
            else:
                self._show(dep, previous+[sgid], indent+[True])



    def _string_sg(self, sgid):
        if "name" not in self.sg_by_id[sgid]:
            name = " N/A "
        elif not self.sg_by_id[sgid]["name"]:
            name = " N/A "
        else:
            name= self.sg_by_id[sgid]["name"]
        return sgid + " ("+ name + ")"

if __name__ == "__main__":
    parser = argparse.ArgumentParser(formatter_class=argparse.RawDescriptionHelpFormatter, description="show AWS security group dependencies", epilog=textwrap.dedent('''
        please setup your boto credentials first.
            here's a few options:
             setup environment varialbes: AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY
             or create one or some of below files (boto will evaluate in order):
                /etc/boto.cfg
                ~/.boto
                ~/.aws/credentials
             and put your credentials in the file(s) with below format:
               [Credentials]
               aws_access_key_id = <your_access_key_here>
               aws_secret_access_key = <your_secret_key_here>'''))
    parser.add_argument("--region", choices=map(lambda x: x.name, regions()), help="region connect to")
    parser.add_argument("--profile", default="default", help="(optional) specify the aws credential profile to use")
    parser.add_argument("--list",action="store_true", help="only output group id/name")
    g = parser.add_mutually_exclusive_group()
    g.add_argument("--obsolete", action="store_true", help="show security group not used by any service")
    g.add_argument("--eni_only", action="store_true", help="show security group only used by eni (elastic network interface)")
    parser.add_argument("security_group", help="security group id or name, id takes precedence, if you have more than one group with same name, this program will show random one, you should use group id instead. leave empty for all groups", default="", nargs="?")
    args=parser.parse_args()
    if args.obsolete:
        Sg_deps(args.region, args.profile).show_obsolete_sg(showlist=args.list)
    elif args.eni_only:
        Sg_deps(args.region, args.profile).show_eni_only_sg(showlist=args.list)
    else:
        Sg_deps(args.region, args.profile).show_sg(args.security_group, showlist=args.list)
