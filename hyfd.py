"""
HyFd as described in Papenbrock et al - A Hybrid Approach to Functional Dependency Discovery (2016)
There's a difference with TANE, the algorithm uses rules of the form []=>something
whereas TANE only uses X=>something where X is not-empty.
Dataset ncvoter has this kind of FDs and yields different results.
"""
from __future__ import print_function

import sys
import logging
import time
import json
import uuid
import argparse
from hyfd_libs.fd_tree import FDTree
from hyfd_libs.pli import PLI
from hyfd_libs.efficiency import Efficiency
from hyfd_libs.boolean_tree import BooleanTree

FORMAT = "[%(filename)s:%(lineno)s - %(funcName)20s() ] %(message)s"

LOG_FILENAME = 'example.log'
logging.basicConfig(filename=LOG_FILENAME, format=FORMAT, level=logging.INFO)
logger = logging.getLogger(__name__)

INVALID_FDS_THRESHOLD = 0.01
EFFICIENCY_THRESHOLD_INIT = 1
LEARNING_FACTOR = 0.5

cache = set([])

def read_csv(path, separator=','):
    """
    READ CSV
    """
    mat = [list(map(str, line.replace('\n','').split(separator))) for line in open(path, 'r').readlines()]
    return mat

def build_pli(lst):
    '''
    Generates a PLI (position list indexes) given a column in the database (lst) (partition)
    Example:
    [0,1,0,1,1,2] -> [[1,3,4], [0,2]]
    PLIs are ordered by number of indices in each component and filtered of singletons
    '''
    hashes = {}
    for i, j in enumerate(lst):
        hashes.setdefault(j, set([])).add(i)
    return sorted([sorted(i) for i in hashes.values() if len(i) > 1], key = lambda k: len(k), reverse=True)

def transpose(lst, n_rows):
    return [[row[j] for row in lst] for j in range(n_rows)]


class HyFd(object):
    def __init__(self, args):
        self.natts = 0
        self.nrecs = 0
        self.records = read_csv(args.db_path, separator=args.separator)
        self.att_order_map = []
        self.non_fds = None
        self.fds = None
        self.current_level = None
        self.current_level_number = None
        self.plis = None
        self.pli_records = None
        self.comparison_suggestions = []
        self.efficiency_queue = []
        self.efficiency_threshold = args.efft
        self.learning_factor = args.lf
        self.invalid_fds_threshold = args.ift
        self.go_on = True
        self.execute()
        
        

    def get_fds(self):
        '''
        Yields pairs of sets representing the functional dependency
        returns (list, list)
        '''
        for lhs, rhs in self.fds.read_fds():
            yield ([self.plis[i].att for i in lhs], [self.plis[i].att for i in rhs])


    def execute(self):
        '''
        Executes HyFD
        '''
        tmp = "/tmp/{}.json".format(str(uuid.uuid4()))
        t0 = time.time()
        
        self.preproc()
        iteration = 1
        try:
            while self.go_on:
                self.sampling()
                self.induction()
                self.validation()
                n_fds = self.fds.n_fds
                print("Iteration:{}, N_FDS:{}, TIME:{}\n".format(iteration, n_fds, time.time()-t0 ))
                logger.info("Iteration:{}, N_FDS:{}, TIME:{}".format(iteration, n_fds, time.time()-t0 ))
                iteration+=1
                with open(tmp, 'w') as fout:
                    json.dump(list(self.get_fds()), fout)
                    logger.info("FDs written in:{}".format(tmp))
        except KeyboardInterrupt:
            tmp = "/tmp/{}.json".format(str(uuid.uuid4()))
            with open(tmp, 'w') as fout:
                json.dump(list(self.get_fds()), fout)
                print("\n\nExiting by command")
                print ("Execution Time: {}".format(time.time()-t0))
                print ("FDs Found: {}".format(self.fds.n_fds))
                print("FDs written in: {}".format(tmp))
            exit()



    def print_records(self):
        '''
        Prints a formated version of the records in the database
        '''
        for ri, record in enumerate(self.pli_records):
            print ("\t"+str(ri)+':'+'|'.join(str(i) if i >= 0 else 'X' for i in record))
        
    def preproc(self):#, records):
        """
        PREPROC as described in algorithm 1 in [1]
        """
        
        self.nrecs = len(self.records)
        self.natts = len(self.records[0])
        logger.info("PREPROCESSING with {} tuples and {} attributes".format(self.nrecs, self.natts))
        PLI._nrecs = self.nrecs

        # INVERT TABLE RECORDS
        self.plis = [PLI(pi, build_pli(i)) for pi, i in enumerate(transpose(self.records, self.natts))]
        
        self.plis.sort(key=lambda x: x.number_of_parts, reverse=True)
        
        self.pli_records = []
        for pli in self.plis:
            col = [-1] * self.nrecs
            for cluster_id, cluster in enumerate(pli):
                for row in cluster:
                    col[row] = cluster_id
            self.pli_records.append(col)
        self.pli_records = transpose(self.pli_records, self.nrecs)
 


    def sampling(self):
        '''
        Sampling as described in algorithm 2 in [1]
        '''
        logger.info("SAMPLING with efficiency_queue of length {}".format(len(self.efficiency_queue)))
        

        if not bool(self.efficiency_queue):
            # self.non_fds = set([])
            self.non_fds = BooleanTree()
            for x, pli in enumerate(self.plis):
                ileft = x-1
                iright = x+1 if x+1 < self.natts else 0
                for cluster_id in range(len(pli)):
                    pli[cluster_id].sort( key=lambda k: self.pli_records[k][ileft] if self.pli_records[k][ileft] >= 0 else self.pli_records[k][iright] )

            for x in range(self.natts):
                efficiency = Efficiency(att=x, pli=self.plis[x])
                run_window(efficiency, self.plis[x], self.pli_records, self.non_fds)
                self.efficiency_queue.append(efficiency)
        else:
            self.efficiency_threshold *= self.learning_factor
            
            for sug in self.comparison_suggestions:
                self.non_fds.append(match(self.pli_records[sug[0]], self.pli_records[sug[1]]))
        
        while True:
            
            self.efficiency_queue.sort(key=lambda k: k.eval(), reverse=True)
            
            best_eff = self.efficiency_queue[0]
            be = best_eff.eval()
            print("\rSampling: Efficiency Queue length:{} | Best Efficiency:{} | Efficiency Threshold:{}".format(len(self.efficiency_queue), be, self.efficiency_threshold), end='')
            sys.stdout.flush()
            
            if best_eff.eval() < self.efficiency_threshold:
                logger.debug("Out by low efficiency")
                break
            best_eff.window += 1
            run_window(best_eff, self.plis[best_eff.att], self.pli_records, self.non_fds)
            
            if best_eff.done:
                del self.efficiency_queue[0]
            if not bool(self.efficiency_queue):
                logger.debug("Out by no candidates")
                self.go_on = False
                break
        print ('')
    
    def induction(self):
        '''
        Induction as defined in algorithm 3 of [1]
        '''
        n = self.non_fds.n_new_elements
        logger.info("INDUCTION with number of non-FDs:{}:".format(n))
        print ('\rInduction: Specializing {}/{} new non-FDs'.format(0, n), end='')
        sys.stdout.flush()
        if self.fds is None:
            self.fds = FDTree(n_atts=self.natts)
            self.fds.add([], list(range(self.natts)))
        
        for lhsi, tuple_match in enumerate(self.non_fds):
            print ('\rInduction: Specializing {}/{} new non-FDs'.format(lhsi+1, n), end='')
            
            sys.stdout.flush()
            lhs = [j for j, v in enumerate(tuple_match) if v]
            rhss = [j for j, v in enumerate(tuple_match) if not v]
            
            self.fds.specialize(lhs, rhss)

        self.non_fd_pointer = len(self.non_fds)
        print ('')
        return self.fds

    def specialize(self, fds, lhs, rhs):
        '''
        SPECIALIZES THE FDTREE WITH AN INVALID FD lhs => rhs
        YIELDS THE NODES CREATED IN THE FDTREE
        '''
        # logger.debug('SPECIALIZE: {}=>{}'.format(lhs, rhs))
        invalid_lhss = list(fds.get_fd_and_generals(lhs, rhs))
        print ('**'*100)
        print (lhs, rhs, invalid_lhss)

        for invalid_lhs in invalid_lhss:
            self.fds.remove(invalid_lhs, rhs)
            for x in range(self.natts):
                if x in lhs or rhs == x:
                    continue
                new_lhs = invalid_lhs.union([x])
                
                # if new_lhs.issubset(set(lhs)):
                #     continue
                if self.fds.fd_has_generals(new_lhs, rhs):
                    continue
                yield self.fds.add(new_lhs, [rhs])

    def refines(self, lhs, rhss):
        '''
        REFINES THE FD BY CHECKING IF THE LHS => RHS FOR EACH RHS IN RHSS
        The implementation of this function is not described in [1], but a 
        description of its functioning is provided.

        This is an interpretation of its description.

        @returns ALL RHS IN RHSS SUCH THAT LHS => RHS IS VALID, IF NONE ARE VALID THEN RETURNS []
        '''

        if not bool(rhss):
            return []
        if not bool(lhs):
            return [i for i in rhss if len(self.plis[i]) == 1 and len(self.plis[i][0]) == self.nrecs]
        '''
        mask maintains the indices of RHSS that are still valid
        the function returns when mask is empty.
        '''
        mask = list(range(len(rhss))) 

        s_lhs = sorted(lhs)
        
        clusters = self.plis[s_lhs[0]]

        signatures = (( (i, [self.pli_records[i][x] for x in s_lhs+rhss]) for i in cluster) for cluster in clusters)
        
        mapping = {}
        for cluster_encoding in signatures:
            cluster_encoding = list(cluster_encoding)
            for ti, row_map in cluster_encoding:
                '''
                row_map has the signature created for the LHS and the RHSS for each attribute
                ti is the row number.
                For example, given a,b=>c we can have:
                ti, row_map = (0, [1,1,0]) 
                meaning that for row 0, columns a,b have values 1,1 and column c has value 0.
                row_map is decoded in two signatures, s1 and s2.
                signature s1 is mapped to signature s2.
                if the map of s1 exists and is not s2, then we have that at least one element in the RHSS
                should be removed from mask.
                '''
                # logger.debug("\t ti:{}|row_map:{}".format(ti, row_map))
                s1, s2 = tuple(row_map[:len(s_lhs)]), tuple(row_map[len(s_lhs):])
                '''
                If -1 is in the signature s1, then it is a singleton and should not be checked.
                '''
                if -1 in s1:
                    continue
                
                if not mapping.get(s1, False): # THE SIGNATURE S1 IS NOT MAPPED, DO IT
                    mapping[s1] = {
                        'ti':[ti],
                        's':s2
                    }
                else: # THERE IS A MAPPING FOR SIGNATURE S1
                    s3 = mapping[s1]['s'] # GET IT
                    # AND CHECK IT AGAINST S2
                    diff = [i for i in mask if s2[i] == -1 or s2[i] != s3[i]]
                    '''
                    Consider the FD a=>b,c,d and that s1 is already mapped to -1 2 1
                    and s2 is 1 2 -1. 
                    We can see that for tuple ti, the values of columns b and d do not
                    coincide to the previous mapping and they should be removed from mask.
                    '''
                    if bool(diff):
                        
                        for tj in mapping[s1]['ti']:
                            # print ("++++", ti, tj)
                            self.comparison_suggestions.append((tj, ti))
                        for i in diff:
                            mask.remove(i)
                    else:
                        mapping[s1]['ti'].append(ti)

                if not bool(mask):
                    break

            if not bool(mask):
                    break

        result = [rhss[i] for i in mask]

        return result
    
    def validation(self):
        '''
        VALIDATION as described in algorithm 4 of [1]
        '''
        logger.info("VALIDATION")
        
        
        logger.debug(list(self.fds.read_fds()))

        if self.current_level is None:
            self.current_level_number = 0
        
        self.current_level = list(self.fds.get_level(self.current_level_number))
        

        comparison_suggestions = []

        print ('\rValidation: Checking {}/{} Nodes in the FDTree'.format(0, len(self.current_level)), end='')
        sys.stdout.flush()
        while bool(self.current_level):
            
            # print ("\tCURRENT_LEVEL_NUMBER: ", self.current_level_number)
            # print ("\tCURRENT_LEVEL: ", self.current_level)
            # VALIDATE ALL FDS ON CURRENT LEVEL
            invalid_fds = []
            num_valid_fds = 0
            for ni, node in enumerate(self.current_level):
                print ('\rValidation: Checking {}/{} Nodes in the FDTree'.format(ni+1, len(self.current_level)), end='')
                sys.stdout.flush()
                lhs = node.get_lhs()
                rhss = node.get_rhss()
                if not bool(rhss):
                    continue
                
                valid_rhss = self.refines(lhs, rhss)

                num_valid_fds += len(valid_rhss)

                invalid_fds.append( (lhs, [i for i in rhss if i not in valid_rhss]) )
                # print (list(self.fds.read_fds()))

            # ADD ALL CHILDREN TO THE NEXT LEVEL
            next_level = []

            for node in self.current_level:
                for child in node.get_children():
                    next_level.append(child)

            # SPECIALIZE ALL INVALID FDs
            # print ("\tSPECIALIZING INVALIDS: ", invalid_fds, "||", list(self.fds.read_fds()))
            for invalid_fd in invalid_fds:
                lhs, rhss = invalid_fd
                # for node in self.specialize(self.fds, lhs, rhs):
                for node in self.fds.specialize(lhs, rhss):
                    if node is not None:
                        next_level.append(node)
                

            self.current_level = next_level
            self.current_level_number += 1
            # JUDGE EFFICIENCY OF VALIDATION PROCESS
            if len(invalid_fds) > self.invalid_fds_threshold * num_valid_fds:
                print ('')
                return self.fds, comparison_suggestions
        print ('')
        return self.fds, set([])

        

def run_window(efficiency, pli, pli_records, non_fds):
    # logger.debug("\tRUN:{} window::{}".format(pli,efficiency.window ))
    # print ("\tRUN:", pli, 'window::',efficiency.window )
    prev_num_non_fds = len(non_fds)
    for cluster in pli:
        # print ('\t\t:: CLUSTER:',cluster, '|', range(len(cluster)-efficiency.window+1))
        for i in range(len(cluster)-efficiency.window+1):
            # logger.debug('\t\t Comparing tuples {} - {}'.format(cluster[i],cluster[i+efficiency.window-1]))
            # # print ('\t\t\t->',)
            pivot = pli_records[cluster[i]]
            partner = pli_records[cluster[i+efficiency.window-1]]
            compare = match(pivot, partner)
            # logger.debug('\t\t\tResult:{} || New?:{}'.format( compare, compare not in non_fds))
            if not all(compare):
                non_fds.append(compare)
            efficiency.increase_comps()
    efficiency.results += len(non_fds) - prev_num_non_fds

def match(row1, row2):
    return tuple([i==j and i>-1 for i, j in zip(row1, row2)])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='HyFD for Python (by VC)')
    parser.add_argument('db_path', metavar='db_path', type=str, help='path to the database')
    parser.add_argument('-s', '--separator', metavar='separator', type=str, help='Value separator', default=",")
    parser.add_argument(
        '-efft',
        metavar='efficiency threshold',
        type=float,
        default=EFFICIENCY_THRESHOLD_INIT
    )
    parser.add_argument(
        '-lf',
        metavar='learning factor',
        type=float,
        default=LEARNING_FACTOR
    )
    parser.add_argument(
        '-ift',
        metavar='invalid fds threshold',
        type=float,
        default=INVALID_FDS_THRESHOLD
    )

    
    HyFd(parser.parse_args())


