// Jenkins ORCHESTRATES; it contains no application logic. Every stage is just
//   python -m pipeline.<stage> --job JOB_ID
// which can be run identically on a developer machine.
//
// Requirements on the agent: Python 3.11+, FFmpeg on PATH (or set FFMPEG_PATH below).
// NOTE: this file has not been executed on a Jenkins server yet (none was available while
// building Milestone 1); the commands it runs were verified locally.

def STAGES = ['acquire', 'knowledge', 'research', 'lesson', 'script',
              'scenes', 'visuals', 'voice', 'captions', 'render', 'validate', 'package']

def py(String args) {
    // Only constants and the generated job id are interpolated into commands. User-supplied
    // text travels through params.json, never through a shell command line.
    if (isUnix()) {
        return sh(returnStdout: true, script: ".venv/bin/python ${args}").trim()
    }
    return bat(returnStdout: true, script: "@.venv\\Scripts\\python.exe ${args}").trim()
}

pipeline {
    agent any
    options {
        timestamps()
        disableConcurrentBuilds()
        timeout(time: 4, unit: 'HOURS')
    }
    parameters {
        string(name: 'TOPIC', defaultValue: 'How TCP works', description: 'What the video should teach')
        text(name: 'TRANSCRIPT_PATHS', defaultValue: '', description: 'Optional. One transcript file path per line (.txt/.srt/.vtt), located in the agent folder <workspace>/transcripts. Prefix a line with a label and a pipe: Creator A | transcripts/a.txt')
        choice(name: 'STYLE', choices: ['technical_explainer'], description: 'Video style')
        string(name: 'TARGET_DURATION', defaultValue: '3', description: 'Target duration in minutes')
        choice(name: 'AUDIENCE', choices: ['beginner', 'intermediate', 'advanced'], description: 'Audience')
        string(name: 'JOB_ID', defaultValue: '', description: 'Existing job to resume/retry (blank = create a new job)')
        booleanParam(name: 'FORCE', defaultValue: false, description: 'Regenerate stages even if already complete')
    }
    stages {
        stage('Setup') {
            steps {
                script {
                    // Forward slashes are fine for Python on Windows too.
                    env.VIDGEN_WORKSPACE_DIR = "${env.WORKSPACE}/workspace"
                    // Transcripts may only be read from this folder (see config/settings.yaml: ingest).
                    env.VIDGEN__ingest__allowed_dirs = "[\"${env.WORKSPACE.replace('\\', '/')}/transcripts\"]"
                    // env.FFMPEG_PATH = 'C:/tools/ffmpeg/bin/ffmpeg.exe'
                    if (isUnix()) {
                        sh 'python3 -m venv .venv && .venv/bin/python -m pip install -q -e .'
                    } else {
                        bat 'python -m venv .venv && .venv\\Scripts\\python.exe -m pip install -q -e .'
                    }
                }
            }
        }
        stage('Create job') {
            steps {
                script {
                    if (params.JOB_ID?.trim()) {
                        env.VIDGEN_JOB_ID = params.JOB_ID.trim()
                    } else {
                        def sources = []
                        (params.TRANSCRIPT_PATHS ?: '').readLines().collect { it.trim() }.findAll { it }.each { line ->
                            def parts = line.split('\\|', 2)
                            def path = parts[-1].trim()
                            if (!path.startsWith('/') && !(path ==~ /^[A-Za-z]:.*/)) {
                                path = "${env.WORKSPACE}/${path}"
                            }
                            sources << (parts.size() == 2 ? [path: path, label: parts[0].trim()] : path)
                        }
                        def payload = [
                            topic      : params.TOPIC,
                            style      : params.STYLE,
                            duration   : params.TARGET_DURATION,
                            audience   : params.AUDIENCE,
                            transcripts: sources,
                        ]
                        writeFile file: 'params.json', text: groovy.json.JsonOutput.toJson(payload)
                        env.VIDGEN_JOB_ID = py('-m pipeline.jobs create --params-file params.json')
                    }
                    echo "Job: ${env.VIDGEN_JOB_ID}"
                    currentBuild.displayName = "#${env.BUILD_NUMBER} ${env.VIDGEN_JOB_ID}"
                }
            }
        }
        stage('Pipeline') {
            steps {
                script {
                    def force = params.FORCE ? ' --force' : ''
                    for (s in STAGES) {
                        // Completed stages are reused automatically, so rerunning a failed
                        // build resumes at the failed stage without repeating paid work.
                        stage(s) {
                            py("-m pipeline.${s} --job ${env.VIDGEN_JOB_ID}${force}")
                        }
                    }
                }
            }
        }
    }
    post {
        always {
            script {
                if (env.VIDGEN_JOB_ID) {
                    def base = "workspace/jobs/${env.VIDGEN_JOB_ID}"
                    archiveArtifacts artifacts: "${base}/final/**, ${base}/job_cost.json, ${base}/manifest.json, ${base}/logs/**, ${base}/validation/**",
                                     allowEmptyArchive: true
                }
            }
        }
        success {
            echo "VIDEO READY FOR REVIEW: ${env.VIDGEN_JOB_ID} (see archived final/ artifacts)"
        }
        failure {
            echo "Failed. Fix the cause and re-run with JOB_ID=${env.VIDGEN_JOB_ID}; completed stages will be reused."
        }
    }
}
