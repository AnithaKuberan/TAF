from Cb_constants import ClusterRun
from TestInput import TestInputSingleton
from testconstants import \
    LINUX_COUCHBASE_BIN_PATH, LINUX_NONROOT_CB_BIN_PATH, \
    WIN_COUCHBASE_BIN_PATH, MAC_COUCHBASE_BIN_PATH


class CbCmdBase:
    def __init__(self, shell_conn, binary_name,
                 username="Administrator", password="password"):

        self.shellConn = shell_conn
        self.port = shell_conn.port
        self.mc_port = shell_conn.memcached_port
        self.username = username
        self.password = password
        self.binaryName = binary_name

        self.cbstatCmd = "%s%s" % (LINUX_COUCHBASE_BIN_PATH, self.binaryName)

        if int(shell_conn.port) in range(ClusterRun.port,
                                         ClusterRun.port+10):
            # Cluster run case
            self.cbstatCmd = "%s/%s" \
                % (TestInputSingleton.input.servers[0].cli_path,
                   self.binaryName)
        elif self.shellConn.extract_remote_info().type.lower() == 'windows':
            # Windows case
            self.cbstatCmd = "%s%s.exe" % (WIN_COUCHBASE_BIN_PATH,
                                           self.binaryName)
        elif self.shellConn.extract_remote_info().type.lower() == 'mac':
            # MacOS case
            self.cbstatCmd = "%s%s" % (MAC_COUCHBASE_BIN_PATH,
                                       self.binaryName)
        elif self.shellConn.username != "root":
            # Linux non-root case
            self.cbstatCmd = "%s%s" % (LINUX_NONROOT_CB_BIN_PATH,
                                       self.binaryName)

    def _execute_cmd(self, cmd):
        """
        Executed the given command in the target shell
        Arguments:
        :cmd - Command to execute

        Returns:
        :output - Output for the command execution
        :error  - Buffer containing warnings/errors from the execution
        """
        return self.shellConn.execute_command(cmd)
