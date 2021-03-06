import Queue
import copy
import json
import random
import re
import testconstants
from threading import Thread

from bucket_collections.collections_base import CollectionBase
from bucket_utils.bucket_ready_functions import BucketUtils, DocLoaderUtils
from collections_helper.collections_spec_constants import MetaCrudParams
from couchbase_helper.tuq_generators import JsonGenerator
from couchbase_helper.tuq_generators import TuqGenerators
from membase.api.rest_client import RestConnection
from n1ql_exceptions import N1qlException
from remote.remote_util import RemoteMachineShellConnection
from random_query_template import WhereClause
from sdk_exceptions import SDKException

from com.couchbase.client.java.json import JsonObject


class N1qlBase(CollectionBase):
    def setUp(self):
        super(N1qlBase, self).setUp()
        self.scan_consistency = self.input.param("scan_consistency",
                                                 'REQUEST_PLUS')
        self.path = testconstants.LINUX_COUCHBASE_BIN_PATH
        self.use_rest = self.input.param("use_rest", True)
        self.hint_index = self.input.param("hint", None)
        self.n1ql_port = self.input.param("n1ql_port", 8093)
        self.analytics = self.input.param("analytics", False)
        self.named_prepare = self.input.param("named_prepare", None)
        self.version = self.input.param("cbq_version", "sherlock")
        self.isprepared = False
        self.ipv6 = self.input.param("ipv6", False)
        self.trans = self.input.param("n1ql_txn", True)
        self.commit = self.input.param("commit", True)
        self.rollback_to_savepoint = self.input.param("rollback_to_savepoint", False)
        self.skip_index = self.input.param("skip_index", False)
        self.primary_indx_type = self.input.param("primary_indx_type", 'GSI')
        self.index_type = self.input.param("index_type", 'GSI')
        self.skip_primary_index = self.input.param("skip_primary_index", False)
        self.flat_json = self.input.param("flat_json", False)
        self.dataset = self.input.param("dataset", "sabre")
        self.array_indexing = self.input.param("array_indexing", False)
        self.max_verify = self.input.param("max_verify", None)
        self.num_stmt_txn = self.input.param("num_stmt_txn", 5)
        self.num_collection = self.input.param("num_collection", 1)
        self.num_savepoints = self.input.param("num_savepoints", 0)
        self.override_savepoint = self.input.param("override_savepoint", 0)
        self.num_buckets = self.input.param("num_buckets", 1)
        self.prepare = self.input.param("prepare_stmt", False)
        self.num_txn = self.input.param("num_txn", 3)
        self.clause = WhereClause()
        self.buckets = self.bucket_util.get_all_buckets()
        self.collection_map = {}
        self.get_random_number_stmt(self.num_stmt_txn)
        self.txtimeout = self.input.param("txntimeout", 0)
        load_spec = self.input.param("load_spec", self.data_spec_name)
        self.num_commit = self.input.param("num_commit", 3)
        self.num_rollback_to_savepoint = \
        self.input.param("num_rollback_to_savepoint", 0)
        self.num_conflict = self.input.param("num_conflict", 0)
        self.write_conflict = self.input.param("write_conflict", False)
        input_spec = self.bucket_util.get_crud_template_from_package(
                                        load_spec)
        self.doc_gen_type = input_spec.get(
                                MetaCrudParams.DOC_GEN_TYPE, "default")
        self.index_map = {}

    def tearDown(self):
        self.drop_index()
        super(N1qlBase, self).tearDown()

    def get_txid(self, records):
        try:
            txid = records["results"][0]["txid"]
        except:
            txid = ""
        return txid

    def create_txn(self, txtimeout=0, durability_level=""):
        query_params = {}
        if self.durability_level:
            query_params["Durability"] = durability_level
        if self.txtimeout:
            query_params["txtimeout"] = str(txtimeout) + "m"
        stmt = "BEGIN WORK"
        results = self.run_cbq_query(stmt, query_params=query_params)
        txid = self.get_txid(results)
        return {'txid': txid}

    def end_txn(self, query_params, commit, savepoint=""):
        # query_params = {'txid': txid}
        if savepoint:
            stmt = "ROLLBACK TO SAVEPOINT " + savepoint
        elif commit:
            stmt = "COMMIT WORK"
        else:
            stmt = "ROLLBACK WORK"
        self.log.info("commit work for txn %s"%query_params)
        result = self.run_cbq_query(stmt, query_params=query_params)
        return stmt, result

    def run_cbq_query(self, query=None, min_output_size=10,
                      server=None, query_params={}, is_prepared=False,
                      encoded_plan=None, username=None, password=None):
        if query is None:
            query = ""
        if server is None:
            server = self.cluster.master
        cred_params = {'creds': []}
        rest = RestConnection(server)
        if username is None and password is None:
            username = 'Administrator'
            password = 'password'
        cred_params['creds'].append({'user': username, 'pass': password})
        for bucket in self.bucket_util.buckets:
            if bucket.saslPassword:
                cred_params['creds'].append({'user': 'local:%s' % bucket.name,
                                             'pass': bucket.saslPassword})
        query_params.update(cred_params)

        if self.use_rest:
            query_params.update({'scan_consistency': self.scan_consistency})
            if hasattr(self, 'query_params') and self.query_params:
                query_params = self.query_params
            if self.hint_index and (query.lower().find('select') != -1):
                from_clause = re.sub(r'let.*', '',
                                     re.sub(r'.*from', '', re.sub(r'where.*', '', query)))
                from_clause = re.sub(r'LET.*', '',
                                     re.sub(r'.*FROM', '', re.sub(r'WHERE.*', '', from_clause)))
                from_clause = re.sub(r'select.*', '', re.sub(r'order by.*', '',
                                                             re.sub(r'group by.*', '',
                                                                    from_clause)))
                from_clause = re.sub(r'SELECT.*', '', re.sub(r'ORDER BY.*', '',
                                                             re.sub(r'GROUP BY.*', '',
                                                                    from_clause)))
                hint = ' USE INDEX (%s using %s) ' % (self.hint_index,
                                                      self.index_type)
                query = query.replace(from_clause, from_clause + hint)

            if not is_prepared:
                self.log.info('RUN QUERY %s' % query)

            if self.analytics:
                query = query + ";"
                for bucket in self.buckets:
                    query = query.replace(bucket.name, bucket.name + "_shadow")
                result1 = RestConnection(self.cluster.cbas_node) \
                    .execute_statement_on_cbas(query, "immediate")
                try:
                    result = json.loads(result1)
                    print result
                except Exception as ex:
                    self.log.error("CANNOT LOAD QUERY RESULT IN JSON: %s"
                                   % ex.message)
                    self.log.error("INCORRECT DOCUMENT IS: " + str(result1))
            else:
                result = rest.query_tool(query, self.n1ql_port,
                                         query_params=query_params,
                                         is_prepared=is_prepared,
                                         named_prepare=self.named_prepare,
                                         encoded_plan=encoded_plan,
                                         servers=self.servers)
        else:
            self.shell = RemoteMachineShellConnection(self.cluster.master)
            if self.version == "git_repo":
                output = self.shell.execute_commands_inside(
                    "$GOPATH/src/github.com/couchbase/query/"
                    + "shell/cbq/cbq ", "", "", "", "", "", "")
                self.shell.disconnect()
            else:
                if not self.isprepared:
                    query = query.replace('"', '\\"')
                    query = query.replace('`', '\\`')
                    if self.ipv6:
                        cmd = "%scbq  -engine=http://%s:%s/ -q -u %s -p %s" % (
                            self.path, server.ip, self.n1ql_port,
                            username, password)
                    else:
                        cmd = "%scbq  -engine=http://%s:%s/ -q -u %s -p %s" % (
                            self.path, server.ip, server.port,
                            username, password)
                    print cmd

                    output = self.shell.execute_commands_inside(
                        cmd, query, "", "", "", "", "")
                    if not (output[0] == '{'):
                        output1 = '{%s' % output
                    else:
                        output1 = output
                    try:
                        result = json.loads(output1)
                    except Exception as ex:
                        self.log.error("CANNOT LOAD QUERY RESULT IN JSON: %s"
                                       % ex.message)
                        self.log.error("INCORRECT DOCUMENT IS: %s " % output1)
        if 'metrics' in result:
            self.log.info("TOTAL ELAPSED TIME: %s" % result["metrics"]["elapsedTime"])

        if isinstance(result, str) or 'errors' in result:
            if "INDEX" in query:
                self.log.info("Index creation failed")
                result = None
            elif N1qlException.CasMismatchException \
                in str(result["errors"][0]["msg"]) \
                or N1qlException.DocumentExistsException \
                in str(result["errors"][0]["msg"]) or \
                N1qlException.DocumentNotFoundException in \
                str(result["errors"][0]["msg"]) or \
                N1qlException.DocumentAlreadyExistsException \
                in str(result["errors"][0]["msg"]):
                    self.log.info("txn failed with error %s"% result)
                    return result
            else:
                self.fail("txn failed with unexpected errors %s"%result)
                result = None
        return result

    def get_collection_name(self, bucket="default",
                            scope="_default", collection="_default"):
        query_default_bucket = bucket + '.' \
                                + scope + '.' + collection
        return query_default_bucket

    def get_random_number_stmt(self, num_txn):
        self.num_insert = random.choice(range(num_txn))
        num_range = num_txn - self.num_insert
        if num_range > 0:
            self.num_update = random.choice(range(num_range))
        self.num_delete = (num_range - self.num_update)

    def get_collections(self):
        keyspaces = []
        collections = self.bucket_util.get_random_collections(
            self.buckets, self.num_collection, "all", self.num_buckets)
        for bucket, scope_dict in collections.items():
            for s_name, c_dict in scope_dict["scopes"].items():
                for c_name, c_data in c_dict["collections"].items():
                    keyspace = self.get_collection_name(bucket, s_name, c_name)
                    keyspaces.append(keyspace)
        return keyspaces

    def process_index_to_create(self, stmts, collection):
        self.index_map[collection] = []
        all_index = []
        for stmt in stmts:
            index=""
            clause = stmt.split(":")
            if len(self.index_map[collection]) == 0:
                self.create_index(collection)
            while index == "":
                index = BucketUtils.get_random_name()
                if ('%' in index) or (index in all_index):
                    index=""
            result = self.create_index(collection, index, clause[-1])
            if result:
                self.index_map[collection].append(index)
                all_index.append(index)

    def create_index(self, collection, index=None, params=[]):
        name = collection.split('.')
        if params:
            query = "CREATE INDEX `%s` ON default:`%s`.`%s`.`%s`(%s)" \
                    " USING GSI" %(index, name[0],
                     name[1], name[2], params)
        else:
            query = "CREATE PRIMARY INDEX " \
              "on default:`%s`.`%s`.`%s` " \
              "USING GSI" \
              % (name[0], name[1], name[2])
        return self.run_cbq_query(query)

    def drop_index(self):
        for collection in self.index_map.keys():
            name = collection.split('.')
            for index in self.index_map[collection]:
                query = "DROP INDEX default:`%s`.`%s`.`%s`.`%s` "\
                        "USING GSI" %(name[0],name[1], name[2],
                                     index)
                _ = self.run_cbq_query(query)

    def get_tuq_results_object(self, gen_load):
        return TuqGenerators(self.log,
                             [self.generate_full_docs_list([gen_load])])

    def gen_docs(self, generic_key="", start=0, end=1, type="default"):
        json_generator = JsonGenerator()
        if type == "employee":
            gen_docs = json_generator.generate_docs_employee_more_field_types(
                            generic_key, docs_per_day=end, start=start)
        else:
            gen_docs = json_generator.generate_earthquake_doc(
                                generic_key, end=end, start=start)
        return gen_docs

    def get_key_from_doc(self, gen_load):
        key_list = []
        doc_gen = copy.deepcopy(gen_load)
        while doc_gen.has_next():
            key, val = next(doc_gen)
            key_list.append(key)
        return key_list

    def get_doc_gen_list(self, collections, keys=False):
        doc_gen_list = {}
        for bucket in self.buckets:
            for s_name, scope in bucket.scopes.items():
                for c_name, c_dict in scope.collections.items():
                    bucket_collection = \
                        self.get_collection_name(bucket.name, s_name, c_name)
                    if isinstance(self.doc_gen_type, list):
                        random.seed(c_name)
                        type = random.choice(self.doc_gen_type)
                    else:
                        type = self.doc_gen_type
                    if keys:
                        key = []
                        for i in range(c_dict.doc_index[0] ,c_dict.doc_index[1]):
                            key.append("test_collections-" + str(i))
                        doc_gen_list[bucket_collection] = key
                    else:
                        doc_gen_list[bucket_collection] = \
                            self.gen_docs("test_collections", c_dict.doc_index[0],
                                          c_dict.doc_index[1], type=type)
        for key, value in doc_gen_list.items():
            if key not in collections:
                doc_gen_list.pop(key)
        return doc_gen_list

    def get_doc_type_collection(self):
        doc_type_list = {}
        for bucket in self.buckets:
            for s_name, scope in bucket.scopes.items():
                for c_name, c_dict in scope.collections.items():
                    collection = \
                        self.get_collection_name(bucket.name, s_name, c_name)
                    if isinstance(self.doc_gen_type, list):
                        random.seed(c_name)
                        doc_type_list[collection] = random.choice(self.doc_gen_type)
                    else:
                        doc_type_list[collection] = self.doc_gen_type
        return doc_type_list

    def generate_full_docs_list(self, gens_load=[], update=False):
        all_docs_list = dict()
        for gen_load in gens_load:
            doc_gen = copy.deepcopy(gen_load)
            while doc_gen.has_next():
                key, val = next(doc_gen)
                try:
                    val = json.loads(val)
                    if isinstance(val, dict) and 'mutated' not in list(val.keys()):
                        if update:
                            val['mutated'] = 1
                        else:
                            val['mutated'] = 0
                    else:
                        val['mutated'] += val['mutated']
                except TypeError:
                    pass
                all_docs_list[key]=val
        return all_docs_list

    def _verify_results(self, actual_result, expected_result, sort_key=""):
        if self.max_verify is not None:
            actual_result = actual_result[:self.max_verify]
            expected_result = expected_result[:self.max_verify]
        self.assertTrue(len(actual_result) == len(expected_result))
        expected_result=sorted(expected_result, key = lambda i: i[sort_key])
        actual_result=sorted(actual_result, key = lambda i: i[sort_key])
        self.log.info(cmp(actual_result, expected_result))

    def validate_update_results(self, index, docs=[], dict_to_add="",
                                query_params={}):
        name = index.split('.')
        query = "SELECT  META().id,* from default:`%s`.`%s`.`%s` " \
                "WHERE META().id in %s" \
                % (name[0], name[1], name[2], docs)
        dict_to_add = dict_to_add.split("=")
        dict_to_add[1] = dict_to_add[1].replace('\'', '')
        result = self.run_cbq_query(query, query_params=query_params)
        collection = index.split('.')[-1]

        for doc in result["results"]:
            value = doc[collection].get(dict_to_add[0].encode())
            if isinstance(value, list):
                value = [x.encode('UTF8') for x in value]
                if isinstance(dict_to_add[1], str):
                    dict_to_add[1] = dict_to_add[1].strip("][").split(', ')
            else:
                value = str(value)
            if value != dict_to_add[1]:
                self.fail("actual %s and expected value %s are different"
                             % (value, dict_to_add[1]))

    def validate_insert_results(self, index, docs, query_params={}):
        # modify this to get values for list of docs
        keys = docs.keys()
        keys = [x.encode('UTF8') for x in keys]
        name = index.split('.')
        query = "SELECT  * from default:`%s`.`%s`.`%s` WHERE META().id in %s"\
                % (name[0], name[1], name[2], keys)
        result = self.run_cbq_query(query, query_params=query_params)
        for doc in result["results"]:
            t = doc.values()[0]
            if t != docs[t["name"]]:
                self.log.info("expected value %s and actual value %s"
                              % (t, docs[t["name"]]))
        if result["metrics"]["resultCount"] != len(keys):
            self.fail("Mismatch in result count %s and num keys_inserted %s"
                      % (result["metrics"]["resultCount"], len(keys)))

    def validate_delete_results(self, index, docs, query_params={}):
        # modify this to get values for list of docs
        name = index.split('.')
        query = "SELECT  META().id,* from default:`%s`.`%s`.`%s` " \
                "WHERE META().id in %s"\
                % (name[0], name[1], name[2], docs)
        result = self.run_cbq_query(query, query_params=query_params)
        self.log.info("delete result is %s"%(result["results"]))
        if result["results"]:
            self.fail("Deleted doc is present %s" %(result["results"]))

    def get_stmt(self, collections):
        doc_type_list = self.get_doc_type_collection()
        stmt = []
        for bucket_col in collections:
            stmt.extend(self.clause.get_where_clause(
                doc_type_list[bucket_col], bucket_col,
                self.num_insert, self.num_update, self.num_delete))
            self.process_index_to_create(stmt, bucket_col)
        stmt = random.sample(stmt, self.num_stmt_txn)
        stmt = self.add_savepoints(stmt)
        return stmt

    def get_prepare_stmt(self, query, query_params):
        queries = []
        prepare = False
        if prepare:
            query = "PREPARE p_stmt as " + query
            queries.append(query)
            _ = self.run_cbq_query(query, query_params=query_params)
            query = "EXECUTE p_stmt"
            queries.append(query)
        else:
            queries.append(query)
        return queries

    def run_update_query(self, clause, query_params):
        name = clause[0].split('.')
        if len(clause) > 5:
            update_query = "UPDATE default:`%s`.`%s`.`%s` USE KEYS %s " \
                           "SET %s RETURNING meta().id"\
                            % (name[0], name[1], name[2], clause[5], clause[3])
        else:
            update_query = "UPDATE default:`%s`.`%s`.`%s` SET %s " \
                           "WHERE %s LIMIT 100 RETURNING meta().id"\
                           % (name[0], name[1], name[2], clause[3], clause[2])
        queries = self.get_prepare_stmt(update_query, query_params)
        result = self.run_cbq_query(queries[-1], query_params=query_params)
        if result["status"] == "success":
            list_docs = [d.get('id').encode() for d in result["results"]]
            self.validate_update_results(clause[0], list_docs, clause[3],
                                         query_params)
        else:
            self.fail("delete query failed %s"%queries[-1])
            list_docs = list()
        return list_docs, queries

    def run_insert_query(self, clause, query_params):
        docs = {}
        name = clause[0].split('.')
        select_query = "SELECT DISTINCT t.name AS k1,t " \
                       "FROM default:`%s`.`%s`.`%s` t WHERE %s LIMIT 10"\
                       % (name[0], name[1], name[2], clause[2])
        query = "INSERT INTO default:`%s`.`%s`.`%s` " \
                "(KEY k1, value t) %s RETURNING *" \
                % (name[0], name[1], name[2], select_query)
        queries = self.get_prepare_stmt(query, query_params)
        result = self.run_cbq_query(queries[-1], query_params=query_params)
        if result["status"] == "success":
            for val in result["results"]:
                t = val.values()[0]
                key = t["name"]
                docs[key] = t
            self.validate_insert_results(clause[0], docs, query_params)
        elif N1qlException.DocumentAlreadyExistsException \
                in str(result["errors"][0]["msg"]):
            docs = {}
        else:
            self.fail("insert query failed %s"%queries[-1])
        return docs, queries

    def run_delete_query(self, clause, query_params):
        name = clause[0].split('.')
        if len(clause) > 5:
            query = "DELETE FROM default:`%s`.`%s`.`%s` " \
                    "USE KEYS %s RETURNING meta().id"\
                    % (name[0], name[1], name[2], clause[5])
        else:
            query = "DELETE FROM default:`%s`.`%s`.`%s` " \
                    "WHERE %s LIMIT 200 RETURNING meta().id"\
                    % (name[0], name[1], name[2], clause[2])
        docs = list()
        queries = self.get_prepare_stmt(query, query_params)
        result = self.run_cbq_query(queries[-1], query_params=query_params)
        if result["status"] == "success":
            docs = [d.get('id').encode() for d in result["results"]]
            self.validate_delete_results(clause[0], docs, query_params)
        else:
            self.fail("delete query failed %s"%queries[-1])
        return docs, queries

    def run_savepoint_query(self, clause, query_params):
        query = "SAVEPOINT %s" % clause[1]
        result = self.run_cbq_query(query, query_params=query_params)
        return result

    def get_savepoint_to_verify(self, savepoint):
        savepoint_txn = random.choice(savepoint).split(":")[0]
        savepoint_txn = [key for key in savepoint if savepoint_txn in key][-1]
        index = savepoint.index(savepoint_txn) + 1
        return savepoint[:index]

    def full_execute_query(self, stmts, commit, query_params={},
                           rollback_to_savepoint=False, write_conflict=False,
                           issleep=0):
        """
        1. collection_map will store the values changed for a collection after savepoint
        it will be re-intialized after each savepoint and the values will be copied
        to collection_savepoint collection_map[collection] = {INSERT:{}, UPDATE:{}, DELETE:[]}
        2. collection savepoint will keep track of all changes and map it to savepoint
         collection savepoint = {savepoint: {
                                 collection1:{
                                 INSERT:{}, UPDATE:{}, DELETE:[]},..
                                 }..}
        3. savepoint list will have the order of savepoints
        """
        collection_savepoint = dict()
        savepoint = list()
        collection_map = dict()
        txid = query_params.values()[0]
        queries = dict()
        queries[txid] = list()
        try:
            for stmt in stmts:
                clause = stmt.split(":")
                if clause[0] == "SAVEPOINT":
                    query = self.run_savepoint_query(clause, query_params)
                    if clause[1] in str(savepoint):
                        str1 = clause[1] + ":" + str(len(collection_savepoint.keys()))
                        collection_savepoint[str1] = copy.deepcopy(collection_map)
                        savepoint.append(str1)
                    else:
                        collection_savepoint[clause[1]] = copy.deepcopy(collection_map)
                        savepoint.append(clause[1])
                    queries[txid].append(query)
                    collection_map = {}
                    continue
                elif clause[0] not in collection_map.keys():
                    collection_map[clause[0]] = \
                                {"INSERT": {}, "UPDATE": {}, "DELETE":[]}
                if clause[1] == "UPDATE":
                    result, query = \
                        self.run_update_query(clause, query_params)
                    queries[txid].append(query)
                    collection_map[clause[0]]["UPDATE"][clause[3]] = result
                if clause[1] == "INSERT":
                    result, query = self.run_insert_query(
                                            clause, query_params)
                    collection_map[clause[0]]["INSERT"].update(result)
                    queries[txid].extend(query)
                if clause[1] == "DELETE":
                    result, query = self.run_delete_query(
                                        clause, query_params)
                    collection_map[clause[0]]["DELETE"].extend(result)
                    queries[txid].extend(query)
            if issleep:
                self.sleep(issleep)
            if write_conflict:
                write_conflict_result = \
                    self.simulate_write_conflict(stmts, random.choice([True, False]))
            if rollback_to_savepoint and (len(savepoint) > 0):
                savepoint = self.get_savepoint_to_verify(savepoint)
                query, result = self.end_txn(query_params, commit,
                                             savepoint[-1].split(':')[0])
                queries[txid].append(query)
            if commit is False:
                savepoint = []
                collection_savepoint = {}
                query, result = self.end_txn(query_params, commit=False)
            else:
                if (not rollback_to_savepoint) or len(savepoint) == 0:
                    collection_savepoint['last'] = copy.deepcopy(collection_map)
                    savepoint.append('last')
                query = "SELECT * FROM system:transactions"
                results = self.run_cbq_query(query)
                self.log.debug(results)
                query, result = self.end_txn(query_params, commit=True)
                if isinstance(result, str) or 'errors' in result:
                    #retry the entire transaction
                    rerun = self.validate_error_during_commit(result,
                                     collection_savepoint, savepoint)
                    if rerun:
                        query_params = self.create_txn()
                        self.full_execute_query(stmts, commit, query_params,
                             rollback_to_savepoint, write_conflict, issleep)
                    else:
                        savepoint = []
                        collection_savepoint = {}
            if write_conflict and write_conflict_result:
                collection_savepoint['last'] = copy.deepcopy(write_conflict_result)
                savepoint.append("last")
            queries[txid].append(query)
        except Exception as e:
            self.log.info(e)
            collection_savepoint = e
        return collection_savepoint, savepoint, queries

    def simulate_write_conflict(self, stmts, commit):
        collection_map = {}
        clause = list()
        for i in range(5):
            stmt = random.choice(stmts)
            clause = stmt.split(":")
            if clause[0] != "SAVEPOINT":
                collection_map[clause[0]] = {"INSERT": {},
                                             "UPDATE": {},
                                             "DELETE": []}
                break
        # create a txn
        query_params = self.create_txn()
        if clause[1] == "UPDATE":
            result, query = \
                self.run_update_query(clause, query_params)
            collection_map[clause[0]]["UPDATE"][clause[3]] = result
        if clause[1] == "INSERT":
            result, query = self.run_insert_query(
                                    clause, query_params)
            collection_map[clause[0]]["INSERT"].update(result)
        if clause[1] == "DELETE":
            result, query = self.run_delete_query(
                                clause, query_params)
            collection_map[clause[0]]["DELETE"].extend(result)

        # commit or rollback a txn
        if commit:
            self.end_txn(query_params, True)
        else:
            self.end_txn(query_params, False)
            collection_map = dict()
        return collection_map

    def add_savepoints(self, stmt):
        for i in range(self.num_savepoints):
            for _ in range(self.override_savepoint):
                save_stmt = "SAVEPOINT:a" + str(random.choice(range(self.num_savepoints)))
                stmt.append(save_stmt)
            save_stmt = "SAVEPOINT:a" + str(i)
            stmt.append(save_stmt)
        random.shuffle(stmt)
        return stmt

    def validate_keys(self, client, key_value, deleted_key):
        # create a client
        # get all the values and validate
        success, fail = client.get_multi(key_value.keys(), 120)
        for key, val in success.items():
            if type(key_value[key]) == JsonObject:
                expected_val = json.loads(key_value[key].toString())
            elif isinstance(key_value[key], dict):
                expected_val = key_value[key]
            else:
                expected_val = json.loads(key_value[key])
            actual_val = json.loads(val['value'].toString())
            if set(expected_val) != set(actual_val):
                self.fail("expected %s and actual %s for key %s are not equal"
                          % (expected_val, actual_val, key))
        for key, val in fail.items():
            if key in deleted_key and SDKException.DocumentNotFoundException \
                    in str(fail[key]["error"]):
                continue
        self.log.info("Expected keys: %s, Actual: %s, Deleted: %s"
                      % (len(success.keys()), len(key_value.keys()),
                         len(deleted_key)))
        DocLoaderUtils.sdk_client_pool.release_client(client)

    def validate_error_during_commit(self, result,
                                      collection_savepoint, savepoint):
        dict_to_verify = {}
        count = 0
        if N1qlException.CasMismatchException \
            in str(result["errors"][0]["msg"]) or \
            N1qlException.DocumentExistsException \
            in str(result["errors"][0]["msg"]):
            for key in savepoint:
                for index in collection_savepoint[key].keys():
                    keys = collection_savepoint[key][index]["INSERT"].keys()
                    try:
                        dict_to_verify[index].extend(keys)
                    except:
                        dict_to_verify[index] = keys
            for index, docs in dict_to_verify.items():
                name = index.split('.')
                docs = [d.encode() for d in docs]
                query = "SELECT  META().id,* from default:`%s`.`%s`.`%s` " \
                    "WHERE META().id in %s"\
                    % (name[0], name[1], name[2], docs)
                self.log.info("query is %s"%query)
                result = self.run_cbq_query(query)
                if result["metrics"]["resultCount"] == 0:
                    count += 1
            if count == len(dict_to_verify.keys()):
                self.log.info("txn failed with CAS mismatch")
                return True
        elif N1qlException.DocumentNotFoundException in \
            str(result["errors"][0]["msg"]):
            for key in savepoint:
                for index in collection_savepoint[key].keys():
                    try:
                        dict_to_verify[index].extend(
                            collection_savepoint[key][index]["DELETE"])
                    except:
                        dict_to_verify[index] = \
                            collection_savepoint[key][index]["DELETE"]
            for index, docs in dict_to_verify.items():
                name = index.split('.')
                docs = [d.encode() for d in docs]
                query = "SELECT  META().id,* from default:`%s`.`%s`.`%s` " \
                        "WHERE META().id in %s"\
                        % (name[0], name[1], name[2], docs)
                self.log.info("query is %s"%query)
                result = self.run_cbq_query(query)
                if result["metrics"]["resultCount"] == len(docs):
                    count += 1
            if count == len(dict_to_verify.keys()):
                self.fail("got %s error when doc exist" %
                                N1qlException.DocumentNotFoundException)
        return False

    def process_value_for_verification(self, bucket_col, doc_gen_list,
                                       results):
        """
        1. get the collection
        2. get its doc_gen
        3. first validate deleted docs
        4. then check updated docs
        5. validate inserted docs
        """
        for collection in bucket_col:
            gen_load = doc_gen_list[collection]
            self.validate_dict = {}
            self.deleted_key = []
            doc_gen = copy.deepcopy(gen_load)
            while doc_gen.has_next():
                key, val = next(doc_gen)
                self.validate_dict[key] = val
            for res in results:
                for savepoint in res[1]:
                    if collection in res[0][savepoint].keys():
                        for key in set(res[0][savepoint][collection]["DELETE"]):
                            self.deleted_key.append(key)
                        for key, val in res[0][savepoint][collection]["INSERT"].items():
                            self.validate_dict[key] = val
                        for key, val in res[0][savepoint][collection]["UPDATE"].items():
                            mutated = key.split("=")
                            for t_id in val:
                                try:
                                    self.validate_dict[t_id][mutated[0]] = \
                                        mutated[1]
                                except:
                                    self.validate_dict[t_id].put(mutated[0],
                                                                 mutated[1])
            bucket_collection = collection.split('.')
            bucket = BucketUtils.get_bucket_obj(self.buckets,
                                                bucket_collection[0])
            client = \
                DocLoaderUtils.sdk_client_pool.get_client_for_bucket(
                    bucket, bucket_collection[1], bucket_collection[2])
            self.validate_keys(client, self.validate_dict, self.deleted_key)

    def thread_txn(self, args):
        stmt = args[0]
        query_params = args[1]
        commit = args[2]
        rollback_to_savepoint = args[3]
        write_conflict = args[4]
        self.log.info("values are %s %s %s %s" % (stmt, commit,
                                                  rollback_to_savepoint,
                                                  write_conflict))
        collection_savepoint, savepoints, queries = \
            self.full_execute_query(stmt, commit, query_params,
                                    rollback_to_savepoint, write_conflict)
        self.log.info("queries executed in txn are %s" % queries)
        return [collection_savepoint, savepoints]

    def get_stmt_for_threads(self, collections, doc_type_list, num_commit,
                             num_rollback_to_savepoint=0, num_conflict=0):
        que = Queue.Queue()
        fail = False
        self.threads = []
        self.results = []
        stmt = []
        for bucket_col in collections:
            self.get_random_number_stmt(self.num_stmt_txn)
            self.log.info("insert, delete and update %s %s %s"
                           % (self.num_insert, self.num_update,
                              self.num_delete))
            stmt.extend(self.clause.get_where_clause(
                doc_type_list[bucket_col], bucket_col, 2, 0, 3))
                #self.num_insert, self.num_update, self.num_delete))
            self.process_index_to_create(stmt, bucket_col)
        random.shuffle(stmt)
        stmt_list = self.__chunks(stmt, int(len(stmt)/self.num_txn))

        for stmt in stmt_list:
            stmt = self.add_savepoints(stmt)
            random.seed(stmt[0])
            num_rollback_to_savepoint, rollback_to_savepoint = \
                self.num_count(num_rollback_to_savepoint)
            num_commit, commit = \
                self.num_count(num_commit)
            num_conflict, conflict = \
                self.num_count(num_conflict)
            query_params = self.create_txn()
            self.threads.append(
                Thread(target=lambda q, arg1: q.put(self.thread_txn(arg1)),
                       args=(que, [stmt, query_params, commit,
                                   rollback_to_savepoint, conflict])))

        for thread in self.threads:
            thread.start()
        for thread in self.threads:
            thread.join()

        while not que.empty():
            result = que.get()
            if isinstance(result[0], dict):
                self.results.append(result)
            else:
                self.log.info(result[0])
                fail = True
        return self.results, fail

    @staticmethod
    def num_count(value):
        if value > 0:
            value = value - 1
            return value, True
        else:
            return 0, False

    @staticmethod
    def __chunks(i_list, n):
        """Yield successive n-sized chunks from input_list."""
        for i in range(0, len(i_list), n):
            yield i_list[i:i + n]
